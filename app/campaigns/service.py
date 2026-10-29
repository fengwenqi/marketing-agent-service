from dataclasses import dataclass, field
from uuid import UUID, uuid4

from app.campaigns.domain import CampaignEvent, CampaignState
from app.campaigns.state_machine import InvalidTransitionError, transition
from app.hermes.ports import ContextIntakeRequest, HermesPort
from app.hermes.result_mapper import map_context_result


class CampaignNotFoundError(KeyError):
    pass


@dataclass
class CampaignRecord:
    id: UUID
    title: str
    state: CampaignState = CampaignState.DRAFT
    context: dict | None = None
    messages: list[dict] = field(default_factory=list)


class CampaignService:
    """第一阶段的应用服务；接口稳定后将存储实现替换成 PostgreSQL Repository。"""

    def __init__(self, hermes: HermesPort) -> None:
        self.hermes = hermes
        self.campaigns: dict[UUID, CampaignRecord] = {}
        self.idempotent_responses: dict[tuple[str, str], dict] = {}

    def create(self, title: str, idempotency_key: str) -> CampaignRecord:
        cache_key = ("create-campaign", idempotency_key)
        cached = self.idempotent_responses.get(cache_key)
        if cached:
            return self.campaigns[UUID(cached["id"])]
        campaign = CampaignRecord(id=uuid4(), title=title)
        self.campaigns[campaign.id] = campaign
        self.idempotent_responses[cache_key] = {"id": str(campaign.id)}
        return campaign

    def get(self, campaign_id: UUID) -> CampaignRecord:
        try:
            return self.campaigns[campaign_id]
        except KeyError as error:
            raise CampaignNotFoundError(campaign_id) from error

    async def submit_message(
        self, campaign_id: UUID, content: str, idempotency_key: str
    ) -> dict:
        cache_key = (f"message:{campaign_id}", idempotency_key)
        if cached := self.idempotent_responses.get(cache_key):
            return cached

        campaign = self.get(campaign_id)
        try:
            collecting_state = transition(campaign.state, CampaignEvent.MESSAGE_RECEIVED)
        except InvalidTransitionError:
            raise

        # 先保存用户原话，再调用外部 Agent；即使 Gateway 暂时失败，用户输入也不会丢失。
        campaign.messages.append({"role": "user", "content": content})
        campaign.state = collecting_state
        result = await self.hermes.run_context_intake(
            ContextIntakeRequest(
                campaign_id=str(campaign_id),
                request_id=f"{campaign_id}:{idempotency_key}",
                message=content,
            )
        )
        campaign.state = transition(campaign.state, map_context_result(result))
        campaign.context = result.model_dump(mode="json")
        campaign.messages.append(
            {"role": "assistant", "content": result.next_action, "payload": campaign.context}
        )
        response = {
            "campaign_id": str(campaign.id),
            "campaign_state": campaign.state,
            "result": campaign.context,
        }
        self.idempotent_responses[cache_key] = response
        return response

