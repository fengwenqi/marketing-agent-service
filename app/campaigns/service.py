import logging
from uuid import UUID, uuid4

from app.campaigns.domain import CampaignEvent, CampaignState
from app.campaigns.repository import CampaignRepository
from app.campaigns.state_machine import transition
from app.hermes.ports import ContextIntakeRequest, HermesPort
from app.hermes.result_mapper import map_context_result
from app.models.business import Campaign, ConversationMessage

logger = logging.getLogger(__name__)


class CampaignNotFoundError(KeyError):
    pass


class ExternalServiceError(RuntimeError):
    pass


class CampaignService:
    """编排数据库事务与 Hermes 调用，不把长时间 Agent 请求包在数据库事务中。"""

    def __init__(self, repository: CampaignRepository, hermes: HermesPort) -> None:
        self.repository = repository
        self.hermes = hermes

    async def create(self, title: str, idempotency_key: str) -> Campaign:
        scope = "create-campaign"
        cached = await self.repository.get_idempotency(scope, idempotency_key)
        if cached:
            campaign = await self.repository.get_campaign(UUID(cached["id"]))
            if campaign is not None:
                return campaign

        campaign = Campaign(id=uuid4(), title=title, state=CampaignState.DRAFT.value)
        self.repository.add_campaign(campaign)
        await self.repository.save_idempotency(
            scope=scope, key=idempotency_key, response={"id": str(campaign.id)}
        )
        await self.repository.session.commit()
        return campaign

    async def get(self, campaign_id: UUID) -> Campaign:
        campaign = await self.repository.get_campaign(campaign_id)
        if campaign is None:
            raise CampaignNotFoundError(campaign_id)
        return campaign

    async def list_messages(self, campaign_id: UUID) -> list[ConversationMessage]:
        if await self.repository.get_campaign(campaign_id) is None:
            raise CampaignNotFoundError(campaign_id)
        return await self.repository.list_messages(campaign_id)

    async def submit_message(
        self, campaign_id: UUID, content: str, idempotency_key: str
    ) -> dict:
        scope = f"message:{campaign_id}"
        cached = await self.repository.get_idempotency(scope, idempotency_key)
        if cached:
            return cached

        campaign = await self.repository.lock_campaign(campaign_id)
        if campaign is None:
            raise CampaignNotFoundError(campaign_id)
        previous_state = CampaignState(campaign.state)
        collecting_state = transition(previous_state, CampaignEvent.MESSAGE_RECEIVED)
        await self.repository.append_message(
            campaign_id=campaign_id,
            role="user",
            content=content,
            payload=None,
            idempotency_key=idempotency_key,
        )
        campaign.state = collecting_state.value
        self.repository.add_transition(
            campaign_id=campaign_id,
            from_state=previous_state.value,
            to_state=collecting_state.value,
            event=CampaignEvent.MESSAGE_RECEIVED.value,
        )
        # 先提交用户消息并释放行锁，避免 LLM 执行期间长期占用数据库连接和锁。
        await self.repository.session.commit()

        request_id = f"{campaign_id}:{idempotency_key}"
        try:
            result = await self.hermes.run_context_intake(
                ContextIntakeRequest(
                    campaign_id=str(campaign_id), request_id=request_id, message=content
                )
            )
        except Exception as error:
            await self.repository.session.rollback()
            failed_campaign = await self.repository.lock_campaign(campaign_id)
            if failed_campaign is not None:
                failed_campaign.state = previous_state.value
                self.repository.add_transition(
                    campaign_id=campaign_id,
                    from_state=collecting_state.value,
                    to_state=previous_state.value,
                    event="HERMES_FAILED",
                )
                self.repository.add_execution(
                    campaign_id=campaign_id,
                    request_id=request_id,
                    skill="marketing-context-intake",
                    stored_session_id=failed_campaign.hermes_stored_session_id,
                    status="FAILED",
                    input_payload={"message": content},
                    error=str(error),
                )
                await self.repository.session.commit()
            logger.exception(
                "Hermes context intake failed: campaign_id=%s request_id=%s",
                campaign_id,
                request_id,
            )
            raise ExternalServiceError("Hermes Gateway is temporarily unavailable") from error

        completed_campaign = await self.repository.lock_campaign(campaign_id)
        if completed_campaign is None:
            raise CampaignNotFoundError(campaign_id)
        result_event = map_context_result(result)
        completed_state = transition(collecting_state, result_event)
        payload = result.model_dump(mode="json")
        completed_campaign.state = completed_state.value
        completed_campaign.context = payload
        await self.repository.append_message(
            campaign_id=campaign_id,
            role="assistant",
            content=result.next_question or result.next_action,
            payload=payload,
            idempotency_key=f"assistant:{idempotency_key}",
        )
        self.repository.add_transition(
            campaign_id=campaign_id,
            from_state=collecting_state.value,
            to_state=completed_state.value,
            event=result_event.value,
        )
        self.repository.add_execution(
            campaign_id=campaign_id,
            request_id=request_id,
            skill="marketing-context-intake",
            stored_session_id=completed_campaign.hermes_stored_session_id,
            status="SUCCEEDED",
            input_payload={"message": content},
            output_payload=payload,
        )
        response = {
            "campaign_id": str(campaign_id),
            "campaign_state": completed_state.value,
            "result": payload,
        }
        await self.repository.save_idempotency(
            scope=scope, key=idempotency_key, response=response
        )
        await self.repository.session.commit()
        return response
