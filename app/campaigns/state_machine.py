from app.campaigns.domain import (
    CampaignEvent,
    CampaignState,
    StageWorkflowEvent,
    StageWorkflowState,
)


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
    (
        CampaignState.STRATEGY_PENDING_CONFIRMATION,
        CampaignEvent.STRATEGY_REVISION_REQUESTED,
    ): CampaignState.STRATEGY_GENERATING,
    (
        CampaignState.STRATEGY_PENDING_CONFIRMATION,
        CampaignEvent.STRATEGY_CONFIRMED,
    ): CampaignState.STRATEGY_CONFIRMED,
    (
        CampaignState.STRATEGY_CONFIRMED,
        CampaignEvent.STAGE_EXECUTION_STARTED,
    ): CampaignState.STAGE_EXECUTION,
}

_STAGE_TRANSITIONS: dict[
    tuple[StageWorkflowState, StageWorkflowEvent], StageWorkflowState
] = {
    (
        StageWorkflowState.NOT_STARTED,
        StageWorkflowEvent.MEDIA_PLAN_REQUESTED,
    ): StageWorkflowState.MEDIA_PLAN_GENERATING,
    (
        StageWorkflowState.MEDIA_PLAN_GENERATING,
        StageWorkflowEvent.MEDIA_PLAN_CREATED,
    ): StageWorkflowState.MEDIA_PLAN_PENDING_CONFIRMATION,
    (
        StageWorkflowState.MEDIA_PLAN_PENDING_CONFIRMATION,
        StageWorkflowEvent.MEDIA_PLAN_REVISION_REQUESTED,
    ): StageWorkflowState.MEDIA_PLAN_GENERATING,
    (
        StageWorkflowState.MEDIA_PLAN_PENDING_CONFIRMATION,
        StageWorkflowEvent.MEDIA_PLAN_CONFIRMED,
    ): StageWorkflowState.MEDIA_PLAN_CONFIRMED,
}


def transition(current: CampaignState, event: CampaignEvent) -> CampaignState:
    """根据显式事件推进状态，防止客户端或 LLM 随意跳过业务步骤。"""

    try:
        return _TRANSITIONS[(current, event)]
    except KeyError as error:
        raise InvalidTransitionError(f"{current} cannot handle {event}") from error


def transition_stage(
    current: StageWorkflowState, event: StageWorkflowEvent
) -> StageWorkflowState:
    """推进单个 A/B/C 阶段，避免阶段状态互相覆盖。"""

    try:
        return _STAGE_TRANSITIONS[(current, event)]
    except KeyError as error:
        raise InvalidTransitionError(f"{current} cannot handle {event}") from error

