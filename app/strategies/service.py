import logging
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4

from app.campaigns.domain import CampaignEvent, CampaignState
from app.campaigns.state_machine import InvalidTransitionError, transition
from app.hermes.ports import (
    HermesOutputValidationError,
    HermesPort,
    StrategyGenerationRequest,
)
from app.models.business import (
    Campaign,
    GateDecision,
    MarketingStrategyFramework,
    MarketingStrategyVersion,
)
from app.strategies.repository import StrategyRepository

logger = logging.getLogger(__name__)


class StrategyNotFoundError(KeyError):
    pass


class StaleStrategyVersionError(RuntimeError):
    pass


class StrategyIdempotencyConflictError(RuntimeError):
    pass


class ExternalStrategyServiceError(RuntimeError):
    pass


class InvalidStrategyOutputError(RuntimeError):
    pass


@dataclass
class StrategySnapshot:
    campaign: Campaign
    framework: MarketingStrategyFramework
    versions: list[MarketingStrategyVersion]
    decisions: list[GateDecision]

    @property
    def current_version(self) -> MarketingStrategyVersion:
        for version in self.versions:
            if version.id == self.framework.current_version_id:
                return version
        raise StrategyNotFoundError("current strategy version not found")


class StrategyService:
    """编排策略版本、Hermes 外部调用和 Gate1，不跨外部调用持有数据库锁。"""

    def __init__(self, repository: StrategyRepository, hermes: HermesPort) -> None:
        self.repository = repository
        self.hermes = hermes

    @staticmethod
    def _execution_prefix(
        campaign_id: UUID, idempotency_scope: str, idempotency_key: str
    ) -> str:
        """生成适合数据库长度限制的稳定内部执行标识。"""
        identity = f"{campaign_id}:{idempotency_scope}:{idempotency_key}"
        return f"strategy:{sha256(identity.encode('utf-8')).hexdigest()}"

    async def get_for_campaign(self, campaign_id: UUID) -> StrategySnapshot:
        campaign = await self.repository.lock_campaign(campaign_id)
        framework = await self.repository.get_framework(campaign_id)
        if campaign is None or framework is None:
            raise StrategyNotFoundError(campaign_id)
        return StrategySnapshot(
            campaign=campaign,
            framework=framework,
            versions=await self.repository.list_versions(framework.id),
            decisions=await self.repository.list_decisions(campaign_id),
        )

    async def generate(self, campaign_id: UUID, idempotency_key: str) -> StrategySnapshot:
        scope = f"strategy-generate:{campaign_id}"
        cached = await self.repository.get_idempotency(scope, idempotency_key)
        if cached:
            return await self.get_for_campaign(campaign_id)

        campaign = await self.repository.lock_campaign(campaign_id)
        if campaign is None:
            raise StrategyNotFoundError(campaign_id)
        previous_state = CampaignState(campaign.state)
        generating_state = transition(previous_state, CampaignEvent.STRATEGY_REQUESTED)
        framework = await self.repository.get_framework(campaign_id, lock=True)
        if framework is None:
            framework = MarketingStrategyFramework(
                id=uuid4(), campaign_id=campaign_id, status="GENERATING"
            )
            self.repository.add_framework(framework)
        else:
            framework.status = "GENERATING"
        campaign.state = generating_state.value
        self.repository.add_transition(
            campaign_id=campaign_id,
            from_state=previous_state.value,
            to_state=generating_state.value,
            event=CampaignEvent.STRATEGY_REQUESTED.value,
        )
        await self.repository.session.commit()

        return await self._run_generation(
            campaign_id=campaign_id,
            framework_id=framework.id,
            context=campaign.context or {},
            current_version=None,
            instruction=None,
            idempotency_scope=scope,
            idempotency_key=idempotency_key,
            failure_state=CampaignState.CONTEXT_READY,
        )

    async def revise(
        self,
        campaign_id: UUID,
        *,
        expected_version: int,
        instruction: str,
        idempotency_key: str,
        actor_id: str,
    ) -> StrategySnapshot:
        scope = f"strategy-revise:{campaign_id}"
        cached = await self.repository.get_idempotency(scope, idempotency_key)
        if cached:
            if cached.get("instruction") != instruction:
                raise StrategyIdempotencyConflictError(
                    "Idempotency-Key was used with different revision instruction"
                )
            return await self.get_for_campaign(campaign_id)

        campaign = await self.repository.lock_campaign(campaign_id)
        framework = await self.repository.get_framework(campaign_id, lock=True)
        if campaign is None or framework is None or framework.current_version_id is None:
            raise StrategyNotFoundError(campaign_id)
        current = await self.repository.get_version(framework.current_version_id)
        if current is None:
            raise StrategyNotFoundError("current strategy version not found")
        if current.version != expected_version:
            raise StaleStrategyVersionError(
                f"expected strategy version {expected_version}, current is {current.version}"
            )
        prefix = self._execution_prefix(campaign_id, scope, idempotency_key)
        previous_execution = await self.repository.latest_execution(prefix)
        if previous_execution and previous_execution.input_payload.get("instruction") != instruction:
            raise StrategyIdempotencyConflictError(
                "Idempotency-Key was used with different revision instruction"
            )

        previous_state = CampaignState(campaign.state)
        generating_state = transition(
            previous_state, CampaignEvent.STRATEGY_REVISION_REQUESTED
        )
        if await self.repository.find_revision_decision(
            campaign_id=campaign_id,
            resource_version_id=current.id,
            reason=instruction,
            actor_id=actor_id,
        ) is None:
            await self.repository.add_gate_decision(
                campaign_id=campaign_id,
                decision="REVISION_REQUESTED",
                reason=instruction,
                actor_id=actor_id,
                resource_id=framework.id,
                resource_version_id=current.id,
            )
        campaign.state = generating_state.value
        framework.status = "GENERATING"
        self.repository.add_transition(
            campaign_id=campaign_id,
            from_state=previous_state.value,
            to_state=generating_state.value,
            event=CampaignEvent.STRATEGY_REVISION_REQUESTED.value,
        )
        await self.repository.session.commit()

        return await self._run_generation(
            campaign_id=campaign_id,
            framework_id=framework.id,
            context=campaign.context or {},
            current_version=current,
            instruction=instruction,
            idempotency_scope=scope,
            idempotency_key=idempotency_key,
            failure_state=CampaignState.STRATEGY_PENDING_CONFIRMATION,
        )

    async def _run_generation(
        self,
        *,
        campaign_id: UUID,
        framework_id: UUID,
        context: dict,
        current_version: MarketingStrategyVersion | None,
        instruction: str | None,
        idempotency_scope: str,
        idempotency_key: str,
        failure_state: CampaignState,
    ) -> StrategySnapshot:
        framework = await self.repository.get_framework(campaign_id)
        if framework is None:
            raise StrategyNotFoundError(campaign_id)
        # HermesExecution.request_id 目前限制为 128 个字符。API 幂等键由调用方提供，
        # 不能假设长度，因此内部执行记录使用稳定哈希；原始幂等键仍完整保存在
        # IdempotencyRecord 中，业务层的去重语义不会丢失。
        prefix = self._execution_prefix(
            campaign_id, idempotency_scope, idempotency_key
        )
        attempt = await self.repository.next_execution_attempt(prefix)
        request_id = f"{prefix}:attempt-{attempt}"
        input_payload = {
            "instruction": instruction,
            "current_version": current_version.version if current_version else None,
        }
        try:
            response = await self.hermes.run_strategy_generation(
                StrategyGenerationRequest(
                    campaign_id=str(campaign_id),
                    request_id=request_id,
                    marketing_context=context,
                    stored_session_id=framework.hermes_stored_session_id,
                    current_strategy=current_version.content if current_version else None,
                    revision_instruction=instruction,
                )
            )
        except Exception as error:
            await self.repository.session.rollback()
            campaign = await self.repository.lock_campaign(campaign_id)
            failed_framework = await self.repository.get_framework(campaign_id, lock=True)
            if campaign and failed_framework:
                campaign.state = failure_state.value
                failed_framework.status = (
                    "PENDING_CONFIRMATION"
                    if failed_framework.current_version_id
                    else "GENERATING"
                )
                self.repository.add_transition(
                    campaign_id=campaign_id,
                    from_state=CampaignState.STRATEGY_GENERATING.value,
                    to_state=failure_state.value,
                    event="STRATEGY_GENERATION_FAILED",
                )
                self.repository.add_execution(
                    campaign_id=campaign_id,
                    request_id=request_id,
                    stored_session_id=failed_framework.hermes_stored_session_id,
                    status="FAILED",
                    input_payload=input_payload,
                    error=str(error),
                )
                await self.repository.session.commit()
            logger.exception("Hermes strategy generation failed for campaign %s", campaign_id)
            if isinstance(error, HermesOutputValidationError):
                raise InvalidStrategyOutputError(
                    "Hermes returned an invalid marketing strategy structure"
                ) from error
            raise ExternalStrategyServiceError(
                "Hermes strategy generation is temporarily unavailable"
            ) from error

        campaign = await self.repository.lock_campaign(campaign_id)
        completed_framework = await self.repository.get_framework(campaign_id, lock=True)
        if campaign is None or completed_framework is None:
            raise StrategyNotFoundError(campaign_id)
        if current_version is not None:
            persisted_current = await self.repository.get_version(current_version.id)
            if persisted_current is None:
                raise StrategyNotFoundError(current_version.id)
            persisted_current.status = "SUPERSEDED"
        next_number = await self.repository.next_version_number(framework_id)
        version = MarketingStrategyVersion(
            id=uuid4(),
            framework_id=framework_id,
            version=next_number,
            parent_version_id=current_version.id if current_version else None,
            status="PENDING_CONFIRMATION",
            revision_instruction=instruction,
            content=response.result.model_dump(mode="json"),
        )
        self.repository.add_version(version)
        await self.repository.session.flush()
        completed_framework.current_version_id = version.id
        completed_framework.status = "PENDING_CONFIRMATION"
        completed_framework.hermes_stored_session_id = response.stored_session_id
        completed_state = transition(
            CampaignState.STRATEGY_GENERATING, CampaignEvent.STRATEGY_CREATED
        )
        campaign.state = completed_state.value
        self.repository.add_transition(
            campaign_id=campaign_id,
            from_state=CampaignState.STRATEGY_GENERATING.value,
            to_state=completed_state.value,
            event=CampaignEvent.STRATEGY_CREATED.value,
        )
        self.repository.add_execution(
            campaign_id=campaign_id,
            request_id=request_id,
            stored_session_id=response.stored_session_id,
            status="SUCCEEDED",
            input_payload=input_payload,
            output_payload=version.content,
        )
        self.repository.save_idempotency(
            scope=idempotency_scope,
            key=idempotency_key,
            response={
                "version_id": str(version.id),
                "instruction": instruction,
            },
        )
        await self.repository.session.commit()
        return await self.get_for_campaign(campaign_id)

    async def confirm(
        self,
        campaign_id: UUID,
        *,
        expected_version: int,
        actor_id: str,
        idempotency_key: str,
    ) -> StrategySnapshot:
        scope = f"strategy-confirm:{campaign_id}"
        cached = await self.repository.get_idempotency(scope, idempotency_key)
        if cached:
            return await self.get_for_campaign(campaign_id)
        campaign = await self.repository.lock_campaign(campaign_id)
        framework = await self.repository.get_framework(campaign_id, lock=True)
        if campaign is None or framework is None or framework.current_version_id is None:
            raise StrategyNotFoundError(campaign_id)
        current = await self.repository.get_version(framework.current_version_id)
        if current is None:
            raise StrategyNotFoundError(framework.current_version_id)
        if current.version != expected_version:
            raise StaleStrategyVersionError(
                f"expected strategy version {expected_version}, current is {current.version}"
            )
        confirmed_state = transition(
            CampaignState(campaign.state), CampaignEvent.STRATEGY_CONFIRMED
        )
        await self.repository.add_gate_decision(
            campaign_id=campaign_id,
            decision="APPROVE",
            reason=None,
            actor_id=actor_id,
            resource_id=framework.id,
            resource_version_id=current.id,
        )
        current.status = "CONFIRMED"
        framework.status = "CONFIRMED"
        framework.confirmed_version_id = current.id
        campaign.state = confirmed_state.value
        self.repository.add_transition(
            campaign_id=campaign_id,
            from_state=CampaignState.STRATEGY_PENDING_CONFIRMATION.value,
            to_state=confirmed_state.value,
            event=CampaignEvent.STRATEGY_CONFIRMED.value,
        )
        self.repository.save_idempotency(
            scope=scope,
            key=idempotency_key,
            response={"version_id": str(current.id), "actor_id": actor_id},
        )
        await self.repository.session.commit()
        return await self.get_for_campaign(campaign_id)
