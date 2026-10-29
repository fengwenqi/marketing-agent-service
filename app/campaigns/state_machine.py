from app.campaigns.domain import CampaignEvent, CampaignState


class InvalidTransitionError(ValueError):
    pass


_TRANSITIONS: dict[tuple[CampaignState, CampaignEvent], CampaignState] = {
    (CampaignState.DRAFT, CampaignEvent.MESSAGE_RECEIVED): CampaignState.CONTEXT_COLLECTING,
    (
        CampaignState.CONTEXT_BLOCKED,
        CampaignEvent.MESSAGE_RECEIVED,
    ): CampaignState.CONTEXT_COLLECTING,
    (
        CampaignState.CONTEXT_COLLECTING,
        CampaignEvent.CONTEXT_BLOCKED,
    ): CampaignState.CONTEXT_BLOCKED,
    (
        CampaignState.CONTEXT_COLLECTING,
        CampaignEvent.CONTEXT_READY,
    ): CampaignState.CONTEXT_READY,
    (
        CampaignState.CONTEXT_READY,
        CampaignEvent.STRATEGY_REQUESTED,
    ): CampaignState.STRATEGY_GENERATING,
    (
        CampaignState.STRATEGY_GENERATING,
        CampaignEvent.STRATEGY_CREATED,
    ): CampaignState.STRATEGY_PENDING_CONFIRMATION,
}


def transition(current: CampaignState, event: CampaignEvent) -> CampaignState:
    """根据显式事件推进状态，防止客户端或 LLM 随意跳过业务步骤。"""

    try:
        return _TRANSITIONS[(current, event)]
    except KeyError as error:
        raise InvalidTransitionError(f"{current} cannot handle {event}") from error

