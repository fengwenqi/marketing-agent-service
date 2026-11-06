import logging
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4

from app.campaigns.domain import (
    CampaignEvent,
    CampaignState,
    GateCode,
    GateDecisionType,
    StageCode,
    StageWorkflowEvent,
    StageWorkflowState,
)
from app.campaigns.state_machine import transition, transition_stage
from app.gates.policy import validate_gate_decision
from app.hermes.contracts import MarketingStrategyResult
from app.hermes.ports import (
    HermesOutputValidationError,
    HermesPort,
    MediaPlanGenerationRequest,
)
from app.media_plans.repository import MediaPlanRepository
from app.media_plans.validation import ConfirmationInputs, validate_media_plan
from app.models.business import (
    CampaignStageWorkflow,
    GateDecision,
    StageMediaPlan,
    StageMediaPlanVersion,
)

logger = logging.getLogger(__name__)


class MediaPlanNotFoundError(KeyError):
    pass


class StageNotOpenError(RuntimeError):
    pass


class StaleStrategySourceError(RuntimeError):
    pass


class StaleMediaPlanVersionError(RuntimeError):
    pass


class MediaPlanIdempotencyConflictError(RuntimeError):
    pass


class InvalidMediaPlanOutputError(RuntimeError):
    pass


class MediaPlanNotConfirmableError(RuntimeError):
    def __init__(self, missing_fields: list[str], validation_errors: list[dict]) -> None:
        super().__init__("media plan is not ready for Gate1.5 confirmation")
        self.missing_fields = missing_fields
        self.validation_errors = validation_errors


class ExternalMediaPlanServiceError(RuntimeError):
    pass


@dataclass
class MediaPlanSnapshot:
    workflow: CampaignStageWorkflow
    plan: StageMediaPlan
    versions: list[StageMediaPlanVersion]
    decisions: list[GateDecision]

    @property
    def current_version(self) -> StageMediaPlanVersion:
        for version in self.versions:
            if version.id == self.plan.current_version_id:
                return version
        raise MediaPlanNotFoundError("current media plan version not found")


class MediaPlanService:
    def __init__(self, repository: MediaPlanRepository, hermes: HermesPort) -> None:
        self.repository = repository
        self.hermes = hermes

    @staticmethod
    def _require_open(stage_code: StageCode) -> None:
        if stage_code is not StageCode.A:
            raise StageNotOpenError(f"stage {stage_code.value} is not open")

    @staticmethod
    def _scope(operation: str, campaign_id: UUID, stage_code: StageCode) -> str:
        return f"media-plan-{operation}:{campaign_id}:{stage_code.value}"

    @staticmethod
    def _execution_prefix(scope: str, idempotency_key: str) -> str:
        digest = sha256(f"{scope}:{idempotency_key}".encode()).hexdigest()
        return f"media-plan:{digest}"

    @staticmethod
    def _inputs_payload(inputs: ConfirmationInputs) -> dict:
        return inputs.model_dump(mode="json")

    async def get_for_stage(
        self, campaign_id: UUID, stage_code: StageCode
    ) -> MediaPlanSnapshot:
        self._require_open(stage_code)
        workflow = await self.repository.get_workflow(campaign_id, stage_code.value)
        plan = await self.repository.get_plan(campaign_id, stage_code.value)
        if workflow is None or plan is None:
            raise MediaPlanNotFoundError(campaign_id)
        return MediaPlanSnapshot(
            workflow=workflow,
            plan=plan,
            versions=await self.repository.list_versions(plan.id),
            decisions=await self.repository.list_decisions(campaign_id, plan.id),
        )

    async def generate(
        self,
        campaign_id: UUID,
        stage_code: StageCode,
        *,
        confirmation_inputs: ConfirmationInputs,
        idempotency_key: str,
    ) -> MediaPlanSnapshot:
        self._require_open(stage_code)
        scope = self._scope("generate", campaign_id, stage_code)
        signature = {
            "confirmation_inputs": self._inputs_payload(confirmation_inputs)
        }
        cached = await self.repository.get_idempotency(scope, idempotency_key)
        if cached:
            if cached.get("request") != signature:
                raise MediaPlanIdempotencyConflictError(
                    "Idempotency-Key was used with different generation parameters"
                )
            return await self.get_for_stage(campaign_id, stage_code)
        prefix = self._execution_prefix(scope, idempotency_key)
        previous_execution = await self.repository.latest_execution(prefix)
        if previous_execution and previous_execution.input_payload.get("request") != signature:
            raise MediaPlanIdempotencyConflictError(
                "Idempotency-Key was used with different generation parameters"
            )

        campaign = await self.repository.lock_campaign(campaign_id)
        framework = await self.repository.get_strategy_framework(campaign_id, lock=True)
        workflow = await self.repository.get_workflow(
            campaign_id, stage_code.value, lock=True
        )
        existing_plan = await self.repository.get_plan(
            campaign_id, stage_code.value, lock=True
        )
        if (
            campaign is None
            or framework is None
            or framework.confirmed_version_id is None
            or workflow is None
        ):
            raise MediaPlanNotFoundError(campaign_id)
        source_version = await self.repository.get_strategy_version(
            framework.confirmed_version_id
        )
        if source_version is None or framework.status != "CONFIRMED":
            raise StaleStrategySourceError("Gate1-confirmed strategy is required")
        if StageWorkflowState(workflow.state) is not StageWorkflowState.NOT_STARTED:
            raise StaleMediaPlanVersionError("A media plan has already been generated")

        workflow.state = transition_stage(
            StageWorkflowState(workflow.state), StageWorkflowEvent.MEDIA_PLAN_REQUESTED
        ).value
        workflow.version += 1
        if CampaignState(campaign.state) is CampaignState.STRATEGY_CONFIRMED:
            campaign.state = transition(
                CampaignState.STRATEGY_CONFIRMED,
                CampaignEvent.STAGE_EXECUTION_STARTED,
            ).value
        if existing_plan is None:
            plan = StageMediaPlan(
                id=uuid4(),
                campaign_id=campaign_id,
                stage_workflow_id=workflow.id,
                stage_code=stage_code.value,
                source_strategy_framework_id=framework.id,
                source_strategy_version_id=source_version.id,
                status="GENERATING",
            )
            self.repository.add_plan(plan)
        elif existing_plan.current_version_id is None:
            plan = existing_plan
            plan.source_strategy_framework_id = framework.id
            plan.source_strategy_version_id = source_version.id
            plan.status = "GENERATING"
        else:
            raise StaleMediaPlanVersionError("A media plan has already been generated")
        await self.repository.session.commit()

        return await self._run_generation(
            campaign_id=campaign_id,
            stage_code=stage_code,
            plan_id=plan.id,
            source_strategy_version_id=source_version.id,
            marketing_context=campaign.context or {},
            confirmed_strategy=source_version.content,
            confirmation_inputs=confirmation_inputs,
            current_version=None,
            instruction=None,
            actor_id=None,
            scope=scope,
            idempotency_key=idempotency_key,
            failure_state=StageWorkflowState.NOT_STARTED,
            request_signature=signature,
        )

    async def revise(
        self,
        campaign_id: UUID,
        stage_code: StageCode,
        *,
        expected_version: int,
        instruction: str | None,
        confirmation_inputs: ConfirmationInputs | None,
        actor_id: str,
        idempotency_key: str,
    ) -> MediaPlanSnapshot:
        self._require_open(stage_code)
        if instruction is None and confirmation_inputs is None:
            raise ValueError("instruction or confirmation_inputs is required")
        scope = self._scope("revise", campaign_id, stage_code)
        requested_inputs = (
            confirmation_inputs.model_dump(mode="json", exclude_unset=True)
            if confirmation_inputs is not None
            else None
        )
        signature = {
            "expected_version": expected_version,
            "instruction": instruction,
            "confirmation_inputs": requested_inputs,
            "actor_id": actor_id,
        }
        cached = await self.repository.get_idempotency(scope, idempotency_key)
        if cached:
            if cached.get("request") != signature:
                raise MediaPlanIdempotencyConflictError(
                    "Idempotency-Key was used with different revision parameters"
                )
            return await self.get_for_stage(campaign_id, stage_code)

        prefix = self._execution_prefix(scope, idempotency_key)
        previous_execution = await self.repository.latest_execution(prefix)
        if previous_execution and previous_execution.input_payload.get("request") != signature:
            raise MediaPlanIdempotencyConflictError(
                "Idempotency-Key was used with different revision parameters"
            )

        campaign = await self.repository.lock_campaign(campaign_id)
        framework = await self.repository.get_strategy_framework(campaign_id, lock=True)
        workflow = await self.repository.get_workflow(
            campaign_id, stage_code.value, lock=True
        )
        plan = await self.repository.get_plan(campaign_id, stage_code.value, lock=True)
        if (
            campaign is None
            or framework is None
            or workflow is None
            or plan is None
            or plan.current_version_id is None
        ):
            raise MediaPlanNotFoundError(campaign_id)
        self._assert_source_current(plan, framework.confirmed_version_id)
        current = await self.repository.get_version(plan.current_version_id)
        if current is None:
            raise MediaPlanNotFoundError(plan.current_version_id)
        if current.version != expected_version:
            raise StaleMediaPlanVersionError(
                f"expected media plan version {expected_version}, current is {current.version}"
            )
        merged_inputs = dict(current.confirmation_inputs)
        if requested_inputs:
            merged_inputs.update(requested_inputs)
        merged_confirmation = ConfirmationInputs.model_validate(merged_inputs)

        reason = instruction or "updated confirmation inputs"
        if await self.repository.find_revision_decision(
            campaign_id=campaign_id,
            resource_version_id=current.id,
            reason=reason,
            actor_id=actor_id,
        ) is None:
            await self.repository.add_gate_decision(
                campaign_id=campaign_id,
                decision="REVISION_REQUESTED",
                reason=reason,
                actor_id=actor_id,
                resource_id=plan.id,
                resource_version_id=current.id,
            )
        workflow.state = transition_stage(
            StageWorkflowState(workflow.state),
            StageWorkflowEvent.MEDIA_PLAN_REVISION_REQUESTED,
        ).value
        workflow.version += 1
        plan.status = "GENERATING"
        await self.repository.session.commit()

        source_version = await self.repository.get_strategy_version(
            plan.source_strategy_version_id
        )
        if source_version is None:
            raise StaleStrategySourceError("source strategy version no longer exists")
        return await self._run_generation(
            campaign_id=campaign_id,
            stage_code=stage_code,
            plan_id=plan.id,
            source_strategy_version_id=source_version.id,
            marketing_context=campaign.context or {},
            confirmed_strategy=source_version.content,
            confirmation_inputs=merged_confirmation,
            current_version=current,
            instruction=instruction,
            actor_id=actor_id,
            scope=scope,
            idempotency_key=idempotency_key,
            failure_state=StageWorkflowState.MEDIA_PLAN_PENDING_CONFIRMATION,
            request_signature=signature,
        )

    async def _run_generation(
        self,
        *,
        campaign_id: UUID,
        stage_code: StageCode,
        plan_id: UUID,
        source_strategy_version_id: UUID,
        marketing_context: dict,
        confirmed_strategy: dict,
        confirmation_inputs: ConfirmationInputs,
        current_version: StageMediaPlanVersion | None,
        instruction: str | None,
        actor_id: str | None,
        scope: str,
        idempotency_key: str,
        failure_state: StageWorkflowState,
        request_signature: dict | None = None,
    ) -> MediaPlanSnapshot:
        plan = await self.repository.get_plan(campaign_id, stage_code.value)
        if plan is None:
            raise MediaPlanNotFoundError(plan_id)
        prefix = self._execution_prefix(scope, idempotency_key)
        attempt = await self.repository.next_execution_attempt(prefix)
        request_id = f"{prefix}:attempt-{attempt}"
        input_payload = {
            "request": request_signature
            or {
                "confirmation_inputs": self._inputs_payload(confirmation_inputs),
                "instruction": instruction,
                "actor_id": actor_id,
            },
            "current_version": current_version.version if current_version else None,
        }
        try:
            response = await self.hermes.run_media_plan_generation(
                MediaPlanGenerationRequest(
                    campaign_id=str(campaign_id),
                    stage_code=stage_code.value,
                    request_id=request_id,
                    marketing_context=marketing_context,
                    confirmed_strategy=confirmed_strategy,
                    confirmation_inputs=self._inputs_payload(confirmation_inputs),
                    stored_session_id=plan.hermes_stored_session_id,
                    current_plan=current_version.content if current_version else None,
                    revision_instruction=instruction,
                )
            )
        except Exception as error:
            await self._record_failure(
                campaign_id=campaign_id,
                stage_code=stage_code,
                request_id=request_id,
                input_payload=input_payload,
                failure_state=failure_state,
                error=error,
            )
            if isinstance(error, HermesOutputValidationError):
                raise InvalidMediaPlanOutputError(
                    "Hermes returned an invalid media plan structure"
                ) from error
            raise ExternalMediaPlanServiceError(
                "Hermes media plan generation is temporarily unavailable"
            ) from error

        framework = await self.repository.get_strategy_framework(campaign_id, lock=True)
        workflow = await self.repository.get_workflow(
            campaign_id, stage_code.value, lock=True
        )
        completed_plan = await self.repository.get_plan(
            campaign_id, stage_code.value, lock=True
        )
        if workflow is None or completed_plan is None or framework is None:
            raise MediaPlanNotFoundError(campaign_id)
        if framework.confirmed_version_id != source_strategy_version_id:
            workflow.state = failure_state.value
            completed_plan.status = "STALE_SOURCE"
            self.repository.add_execution(
                campaign_id=campaign_id,
                request_id=request_id,
                stored_session_id=completed_plan.hermes_stored_session_id,
                status="FAILED",
                input_payload=input_payload,
                error="confirmed strategy changed during media plan generation",
            )
            await self.repository.session.commit()
            raise StaleStrategySourceError(
                "confirmed strategy changed during media plan generation"
            )
        if current_version is not None:
            persisted_current = await self.repository.get_version(current_version.id)
            if (
                persisted_current is None
                or completed_plan.current_version_id != persisted_current.id
            ):
                await self.repository.session.rollback()
                raise StaleMediaPlanVersionError("media plan changed during generation")
        strategy = MarketingStrategyResult.model_validate(confirmed_strategy)
        A_strategy = next(stage for stage in strategy.stages if stage.code == "A")
        validation = validate_media_plan(response.result, confirmation_inputs, A_strategy)
        if current_version is not None:
            persisted_current.status = "SUPERSEDED"
        version = StageMediaPlanVersion(
            id=uuid4(),
            media_plan_id=plan_id,
            version=await self.repository.next_version_number(plan_id),
            parent_version_id=current_version.id if current_version else None,
            status="PENDING_CONFIRMATION",
            revision_instruction=instruction,
            confirmation_inputs=self._inputs_payload(confirmation_inputs),
            missing_confirmation_fields=validation.missing_fields,
            validation_result=validation.model_dump(mode="json"),
            content=response.result.model_dump(mode="json"),
        )
        self.repository.add_version(version)
        await self.repository.session.flush()
        completed_plan.current_version_id = version.id
        completed_plan.status = "PENDING_CONFIRMATION"
        completed_plan.hermes_stored_session_id = response.stored_session_id
        workflow.state = transition_stage(
            StageWorkflowState.MEDIA_PLAN_GENERATING,
            StageWorkflowEvent.MEDIA_PLAN_CREATED,
        ).value
        workflow.version += 1
        self.repository.add_execution(
            campaign_id=campaign_id,
            request_id=request_id,
            stored_session_id=response.stored_session_id,
            status="SUCCEEDED",
            input_payload=input_payload,
            output_payload=version.content,
        )
        self.repository.save_idempotency(
            scope=scope,
            key=idempotency_key,
            response={
                "version_id": str(version.id),
                "request": input_payload["request"],
            },
        )
        await self.repository.session.commit()
        return await self.get_for_stage(campaign_id, stage_code)

    async def _record_failure(
        self,
        *,
        campaign_id: UUID,
        stage_code: StageCode,
        request_id: str,
        input_payload: dict,
        failure_state: StageWorkflowState,
        error: Exception,
    ) -> None:
        await self.repository.session.rollback()
        workflow = await self.repository.get_workflow(
            campaign_id, stage_code.value, lock=True
        )
        plan = await self.repository.get_plan(campaign_id, stage_code.value, lock=True)
        if workflow is None or plan is None:
            return
        workflow.state = failure_state.value
        plan.status = (
            "PENDING_CONFIRMATION"
            if plan.current_version_id
            else "GENERATING"
        )
        self.repository.add_execution(
            campaign_id=campaign_id,
            request_id=request_id,
            stored_session_id=plan.hermes_stored_session_id,
            status="FAILED",
            input_payload=input_payload,
            error=str(error),
        )
        await self.repository.session.commit()
        logger.exception("Hermes media plan generation failed for campaign %s", campaign_id)

    @staticmethod
    def _assert_source_current(
        plan: StageMediaPlan, confirmed_version_id: UUID | None
    ) -> None:
        if confirmed_version_id != plan.source_strategy_version_id:
            raise StaleStrategySourceError("media plan source strategy is no longer confirmed")

    async def confirm(
        self,
        campaign_id: UUID,
        stage_code: StageCode,
        *,
        expected_version: int,
        actor_id: str,
        idempotency_key: str,
    ) -> MediaPlanSnapshot:
        self._require_open(stage_code)
        scope = self._scope("confirm", campaign_id, stage_code)
        signature = {"expected_version": expected_version, "actor_id": actor_id}
        cached = await self.repository.get_idempotency(scope, idempotency_key)
        if cached:
            if cached.get("request") != signature:
                raise MediaPlanIdempotencyConflictError(
                    "Idempotency-Key was used with different confirmation parameters"
                )
            return await self.get_for_stage(campaign_id, stage_code)
        framework = await self.repository.get_strategy_framework(campaign_id, lock=True)
        workflow = await self.repository.get_workflow(
            campaign_id, stage_code.value, lock=True
        )
        plan = await self.repository.get_plan(campaign_id, stage_code.value, lock=True)
        if (
            framework is None
            or workflow is None
            or plan is None
            or plan.current_version_id is None
        ):
            raise MediaPlanNotFoundError(campaign_id)
        self._assert_source_current(plan, framework.confirmed_version_id)
        current = await self.repository.get_version(plan.current_version_id)
        if current is None:
            raise MediaPlanNotFoundError(plan.current_version_id)
        if current.version != expected_version:
            raise StaleMediaPlanVersionError(
                f"expected media plan version {expected_version}, current is {current.version}"
            )
        validation = current.validation_result
        if current.missing_confirmation_fields or not validation.get("is_confirmable"):
            raise MediaPlanNotConfirmableError(
                current.missing_confirmation_fields,
                validation.get("errors", []),
            )
        validate_gate_decision(
            StageWorkflowState(workflow.state),
            GateCode.GATE_1_5,
            GateDecisionType.APPROVE,
        )
        await self.repository.add_gate_decision(
            campaign_id=campaign_id,
            decision="APPROVE",
            reason=None,
            actor_id=actor_id,
            resource_id=plan.id,
            resource_version_id=current.id,
        )
        current.status = "CONFIRMED"
        plan.status = "CONFIRMED"
        plan.confirmed_version_id = current.id
        workflow.state = transition_stage(
            StageWorkflowState(workflow.state), StageWorkflowEvent.MEDIA_PLAN_CONFIRMED
        ).value
        workflow.version += 1
        self.repository.save_idempotency(
            scope=scope,
            key=idempotency_key,
            response={"version_id": str(current.id), "request": signature},
        )
        await self.repository.session.commit()
        return await self.get_for_stage(campaign_id, stage_code)
