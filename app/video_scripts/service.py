import hashlib
import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from app.campaigns.domain import (
    GateCode,
    GateDecisionType,
    StageCode,
    StageWorkflowEvent,
    StageWorkflowState,
)
from app.campaigns.state_machine import transition_stage
from app.gates.policy import validate_gate_decision
from app.hermes.contracts import StageMediaPlanResult
from app.hermes.ports import HermesPort, VideoScriptGenerationRequest
from app.models.business import (
    CampaignStageWorkflow,
    GateDecision,
    VideoScript,
    VideoScriptBatch,
    VideoScriptConfirmationSet,
    VideoScriptJob,
    VideoScriptVersion,
)
from app.video_scripts.repository import VideoScriptRepository
from app.video_scripts.validation import validate_video_script


class VideoScriptBatchError(RuntimeError):
    pass


class StageNotOpenError(VideoScriptBatchError):
    pass


class VideoScriptBatchNotFoundError(VideoScriptBatchError):
    pass


class MediaPlanNotConfirmedError(VideoScriptBatchError):
    pass


class NoRetryableScriptJobsError(VideoScriptBatchError):
    pass


class StaleVideoScriptVersionError(VideoScriptBatchError):
    pass


class VideoScriptAlreadyConfirmedError(VideoScriptBatchError):
    pass


class ExternalVideoScriptServiceError(VideoScriptBatchError):
    pass


class InvalidVideoScriptOutputError(VideoScriptBatchError):
    pass


class ScriptBatchConfirmationConflictError(VideoScriptBatchError):
    pass


@dataclass(frozen=True)
class ExpectedScriptVersion:
    script_id: UUID
    version_id: UUID


@dataclass(frozen=True)
class VideoScriptSnapshot:
    script: VideoScript
    current_version: VideoScriptVersion
    versions: list[VideoScriptVersion]


@dataclass(frozen=True)
class VideoScriptBatchSnapshot:
    batch: VideoScriptBatch
    workflow: CampaignStageWorkflow
    jobs: list[VideoScriptJob]
    scripts: list[VideoScript]
    counts: dict[str, int]
    confirmation_set: VideoScriptConfirmationSet | None
    gate_decisions: list[GateDecision]


class VideoScriptBatchService:
    def __init__(self, repository: VideoScriptRepository) -> None:
        self.repository = repository

    @staticmethod
    def _require_open(stage_code: StageCode) -> None:
        if stage_code is not StageCode.A:
            raise StageNotOpenError(f"stage {stage_code.value} is not open")

    async def get_batch(self, campaign_id: UUID, stage_code: StageCode) -> VideoScriptBatchSnapshot:
        self._require_open(stage_code)
        workflow = await self.repository.get_workflow(campaign_id, stage_code.value)
        batch = await self.repository.get_batch(campaign_id, stage_code.value)
        if workflow is None or batch is None:
            raise VideoScriptBatchNotFoundError(campaign_id)
        return VideoScriptBatchSnapshot(
            batch=batch,
            workflow=workflow,
            jobs=await self.repository.list_jobs(batch.id),
            scripts=await self.repository.list_scripts(batch.id),
            counts=await self.repository.job_counts(batch.id),
            confirmation_set=await self.repository.get_confirmation_set(batch.id),
            gate_decisions=await self.repository.list_gate_decisions(campaign_id, batch.id),
        )

    async def get_batch_by_id(self, batch_id: UUID) -> VideoScriptBatchSnapshot:
        batch = await self.repository.get_batch_by_id(batch_id)
        if batch is None:
            raise VideoScriptBatchNotFoundError(batch_id)
        return await self.get_batch(batch.campaign_id, StageCode(batch.stage_code))

    async def create_or_get(
        self, campaign_id: UUID, stage_code: StageCode, idempotency_key: str
    ) -> VideoScriptBatchSnapshot:
        self._require_open(stage_code)
        scope = f"video-script-batch-create:{campaign_id}:{stage_code.value}"
        cached = await self.repository.get_idempotency(scope, idempotency_key)
        if cached:
            return await self.get_batch(campaign_id, stage_code)

        workflow = await self.repository.get_workflow(campaign_id, stage_code.value, lock=True)
        plan = await self.repository.get_media_plan(campaign_id, stage_code.value, lock=True)
        if plan is not None and plan.confirmed_version_id is not None:
            existing = await self.repository.get_batch_by_source_version(plan.confirmed_version_id)
            if existing is not None:
                return await self.get_batch(campaign_id, stage_code)
        if (
            workflow is None
            or plan is None
            or plan.status != "CONFIRMED"
            or plan.confirmed_version_id is None
            or workflow.state != StageWorkflowState.MEDIA_PLAN_CONFIRMED.value
        ):
            raise MediaPlanNotConfirmedError(campaign_id)
        plan_version = await self.repository.get_media_plan_version(plan.confirmed_version_id)
        if plan_version is None:
            raise MediaPlanNotConfirmedError(plan.confirmed_version_id)
        media_plan = StageMediaPlanResult.model_validate(plan_version.content)
        batch = VideoScriptBatch(
            id=uuid4(),
            campaign_id=campaign_id,
            stage_workflow_id=workflow.id,
            source_media_plan_id=plan.id,
            source_media_plan_version_id=plan_version.id,
            stage_code=stage_code.value,
            status="PENDING",
            total_count=15,
        )
        self.repository.add_batch(batch)
        for slot in media_plan.creative_slots:
            self.repository.add_job(
                VideoScriptJob(
                    id=uuid4(),
                    batch_id=batch.id,
                    creative_slot_id=slot.id,
                    status="PENDING",
                    attempt_count=0,
                )
            )
        workflow.state = transition_stage(
            StageWorkflowState(workflow.state),
            StageWorkflowEvent.VIDEO_SCRIPTS_REQUESTED,
        ).value
        workflow.version += 1
        self.repository.save_idempotency(
            scope,
            idempotency_key,
            {"batch_id": str(batch.id), "source_version_id": str(plan_version.id)},
        )
        try:
            await self.repository.session.commit()
        except IntegrityError:
            await self.repository.session.rollback()
            existing = await self.repository.get_batch_by_source_version(plan_version.id)
            if existing is None:
                raise
        return await self.get_batch(campaign_id, stage_code)

    async def retry_failed(
        self, campaign_id: UUID, stage_code: StageCode, idempotency_key: str
    ) -> VideoScriptBatchSnapshot:
        self._require_open(stage_code)
        scope = f"video-script-batch-retry:{campaign_id}:{stage_code.value}"
        if await self.repository.get_idempotency(scope, idempotency_key):
            return await self.get_batch(campaign_id, stage_code)
        workflow = await self.repository.get_workflow(campaign_id, stage_code.value, lock=True)
        batch = await self.repository.get_batch(campaign_id, stage_code.value, lock=True)
        if workflow is None or batch is None:
            raise VideoScriptBatchNotFoundError(campaign_id)
        failed = await self.repository.list_jobs_with_status(batch.id, {"FAILED_RETRYABLE"})
        if not failed:
            raise NoRetryableScriptJobsError(batch.id)
        for job in failed:
            job.status = "PENDING"
            job.last_error_code = None
            job.last_error_message = None
            job.started_at = None
            job.finished_at = None
        batch.status = "RUNNING"
        workflow.state = transition_stage(
            StageWorkflowState(workflow.state),
            StageWorkflowEvent.VIDEO_SCRIPTS_RETRY_REQUESTED,
        ).value
        workflow.version += 1
        self.repository.save_idempotency(scope, idempotency_key, {"batch_id": str(batch.id)})
        await self.repository.session.commit()
        return await self.get_batch(campaign_id, stage_code)


class VideoScriptWorkflowService:
    def __init__(self, repository: VideoScriptRepository, hermes: HermesPort) -> None:
        self.repository = repository
        self.hermes = hermes

    async def get_script(self, campaign_id: UUID, script_id: UUID) -> VideoScriptSnapshot:
        script = await self.repository.get_script(campaign_id, script_id)
        if script is None or script.current_version_id is None:
            raise VideoScriptBatchNotFoundError(script_id)
        current = await self.repository.get_script_version(script.current_version_id)
        if current is None:
            raise VideoScriptBatchNotFoundError(script.current_version_id)
        return VideoScriptSnapshot(
            script=script,
            current_version=current,
            versions=await self.repository.list_script_versions(script.id),
        )

    async def revise_script(
        self,
        campaign_id: UUID,
        script_id: UUID,
        *,
        expected_version: int,
        instruction: str,
        actor_id: str,
        idempotency_key: str,
    ) -> VideoScriptSnapshot:
        scope = f"video-script-revise:{campaign_id}:{script_id}"
        signature = {
            "expected_version": expected_version,
            "instruction": instruction,
            "actor_id": actor_id,
        }
        cached = await self.repository.get_idempotency(scope, idempotency_key)
        if cached:
            if cached.get("request") != signature:
                raise ScriptBatchConfirmationConflictError(
                    "Idempotency-Key was used with different revision parameters"
                )
            return await self.get_script(campaign_id, script_id)

        execution_prefix = f"video-script-revise:{script_id}:{idempotency_key}"
        previous_execution = await self.repository.latest_execution(execution_prefix)
        if previous_execution and previous_execution.input_payload.get("request") != signature:
            raise ScriptBatchConfirmationConflictError(
                "Idempotency-Key was used with different revision parameters"
            )

        script = await self.repository.get_script(campaign_id, script_id, lock=True)
        if script is None or script.current_version_id is None:
            raise VideoScriptBatchNotFoundError(script_id)
        batch = await self.repository.get_batch_by_id(script.batch_id, lock=True)
        if batch is None:
            raise VideoScriptBatchNotFoundError(script.batch_id)
        if batch.status == "CONFIRMED" or script.status == "CONFIRMED":
            raise VideoScriptAlreadyConfirmedError(script_id)
        current = await self.repository.get_script_version(script.current_version_id)
        if current is None:
            raise VideoScriptBatchNotFoundError(script.current_version_id)
        if current.version != expected_version:
            raise StaleVideoScriptVersionError(
                f"expected script version {expected_version}, current is {current.version}"
            )
        plan = await self.repository.get_media_plan(campaign_id, script.stage_code)
        plan_version = await self.repository.get_media_plan_version(
            script.source_media_plan_version_id
        )
        campaign = await self.repository.get_campaign(campaign_id)
        if plan is None or plan_version is None or campaign is None:
            raise VideoScriptBatchNotFoundError(campaign_id)
        strategy = await self.repository.get_strategy_version(plan.source_strategy_version_id)
        if strategy is None:
            raise VideoScriptBatchNotFoundError(plan.source_strategy_version_id)
        slot = next(
            item
            for item in plan_version.content["creative_slots"]
            if item["id"] == script.creative_slot_id
        )
        script.status = "GENERATING"
        await self.repository.session.commit()

        attempt = await self.repository.next_execution_attempt(execution_prefix)
        request_id = f"{execution_prefix}:attempt-{attempt}"
        try:
            response = await self.hermes.run_video_script_generation(
                VideoScriptGenerationRequest(
                    campaign_id=str(campaign_id),
                    stage_code=script.stage_code,
                    request_id=request_id,
                    marketing_context=campaign.context or {},
                    confirmed_strategy=strategy.content,
                    confirmed_media_plan=plan_version.content,
                    creative_slot=slot,
                    stored_session_id=script.hermes_stored_session_id,
                    current_script=current.content,
                    revision_instruction=instruction,
                )
            )
            validation = validate_video_script(response.result, slot)
            if not validation.valid:
                raise InvalidVideoScriptOutputError(validation.model_dump_json())
        except Exception as error:
            await self.repository.session.rollback()
            failed_script = await self.repository.get_script(campaign_id, script_id, lock=True)
            if failed_script is not None:
                failed_script.status = "PENDING_CONFIRMATION"
                self.repository.add_execution(
                    campaign_id=campaign_id,
                    request_id=request_id,
                    stored_session_id=failed_script.hermes_stored_session_id,
                    status="FAILED",
                    input_payload={"request": signature},
                    error=str(error),
                )
                await self.repository.session.commit()
            if isinstance(error, InvalidVideoScriptOutputError):
                raise
            raise ExternalVideoScriptServiceError(
                "Hermes video script generation is temporarily unavailable"
            ) from error

        persisted = await self.repository.get_script(campaign_id, script_id, lock=True)
        if persisted is None or persisted.current_version_id != current.id:
            await self.repository.session.rollback()
            raise StaleVideoScriptVersionError("script changed during revision")
        current.status = "SUPERSEDED"
        version = VideoScriptVersion(
            id=uuid4(),
            video_script_id=script_id,
            version=await self.repository.next_script_version(script_id),
            parent_version_id=current.id,
            status="PENDING_CONFIRMATION",
            revision_instruction=instruction,
            validation_result=validation.model_dump(mode="json"),
            content=response.result.model_dump(mode="json"),
        )
        self.repository.add_version(version)
        await self.repository.session.flush()
        persisted.current_version_id = version.id
        persisted.status = "PENDING_CONFIRMATION"
        persisted.hermes_stored_session_id = response.stored_session_id
        self.repository.add_execution(
            campaign_id=campaign_id,
            request_id=request_id,
            stored_session_id=response.stored_session_id,
            status="SUCCEEDED",
            input_payload={"request": signature},
            output_payload=version.content,
        )
        self.repository.save_idempotency(
            scope,
            idempotency_key,
            {"request": signature, "version_id": str(version.id)},
        )
        await self.repository.session.commit()
        return await self.get_script(campaign_id, script_id)

    async def confirm_batch(
        self,
        campaign_id: UUID,
        stage_code: StageCode,
        *,
        expected_script_versions: list[ExpectedScriptVersion],
        actor_id: str,
        idempotency_key: str,
    ) -> VideoScriptBatchSnapshot:
        VideoScriptBatchService._require_open(stage_code)
        scope = f"video-script-batch-confirm:{campaign_id}:{stage_code.value}"
        requested_manifest = sorted(
            (
                {"script_id": str(item.script_id), "version_id": str(item.version_id)}
                for item in expected_script_versions
            ),
            key=lambda item: item["script_id"],
        )
        signature = {"manifest": requested_manifest, "actor_id": actor_id}
        cached = await self.repository.get_idempotency(scope, idempotency_key)
        if cached:
            if cached.get("request") != signature:
                raise ScriptBatchConfirmationConflictError(
                    "Idempotency-Key was used with different Gate2 parameters"
                )
            return await VideoScriptBatchService(self.repository).get_batch(campaign_id, stage_code)
        batch = await self.repository.get_batch(campaign_id, stage_code.value, lock=True)
        workflow = await self.repository.get_workflow(campaign_id, stage_code.value, lock=True)
        if batch is None or workflow is None:
            raise VideoScriptBatchNotFoundError(campaign_id)
        if batch.status == "CONFIRMED":
            raise VideoScriptAlreadyConfirmedError(batch.id)
        counts = await self.repository.job_counts(batch.id)
        scripts = await self.repository.list_scripts(batch.id, lock=True)
        if (
            batch.status != "COMPLETED"
            or counts["succeeded"] != 15
            or len(scripts) != 15
            or len(expected_script_versions) != 15
        ):
            raise ScriptBatchConfirmationConflictError("script batch is not ready for Gate2")
        expected_map = {item.script_id: item.version_id for item in expected_script_versions}
        if len(expected_map) != 15 or any(
            expected_map.get(script.id) != script.current_version_id for script in scripts
        ):
            raise ScriptBatchConfirmationConflictError(
                "Gate2 script version set no longer matches current versions"
            )
        validate_gate_decision(
            StageWorkflowState(workflow.state),
            GateCode.GATE_2,
            GateDecisionType.APPROVE,
        )
        manifest = [
            {
                "video_script_id": str(script.id),
                "video_script_version_id": str(script.current_version_id),
                "creative_slot_id": script.creative_slot_id,
            }
            for script in scripts
        ]
        manifest.sort(key=lambda item: item["creative_slot_id"])
        digest = hashlib.sha256(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        confirmation_set = VideoScriptConfirmationSet(
            id=uuid4(),
            batch_id=batch.id,
            manifest=manifest,
            content_digest=digest,
        )
        self.repository.add_confirmation_set(confirmation_set)
        await self.repository.session.flush()
        for script in scripts:
            current = await self.repository.get_script_version(script.current_version_id)
            if current is None:
                raise ScriptBatchConfirmationConflictError("current script version is missing")
            current.status = "CONFIRMED"
            script.status = "CONFIRMED"
            script.confirmed_version_id = current.id
        await self.repository.add_gate_decision(
            campaign_id=campaign_id,
            actor_id=actor_id,
            batch_id=batch.id,
            confirmation_set_id=confirmation_set.id,
        )
        batch.status = "CONFIRMED"
        workflow.state = transition_stage(
            StageWorkflowState(workflow.state),
            StageWorkflowEvent.VIDEO_SCRIPTS_CONFIRMED,
        ).value
        workflow.version += 1
        self.repository.save_idempotency(
            scope,
            idempotency_key,
            {"request": signature, "confirmation_set_id": str(confirmation_set.id)},
        )
        await self.repository.session.commit()
        return await VideoScriptBatchService(self.repository).get_batch(campaign_id, stage_code)
