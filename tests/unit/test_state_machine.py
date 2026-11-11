import pytest

from app.campaigns.domain import (
    CampaignEvent,
    CampaignState,
    StageWorkflowEvent,
    StageWorkflowState,
)
from app.campaigns.state_machine import (
    InvalidTransitionError,
    transition,
    transition_stage,
)


@pytest.mark.parametrize(
    ("current", "event", "expected"),
    [
        (CampaignState.DRAFT, CampaignEvent.MESSAGE_RECEIVED, CampaignState.CONTEXT_COLLECTING),
        (
            CampaignState.CONTEXT_COLLECTING,
            CampaignEvent.CONTEXT_BLOCKED,
            CampaignState.CONTEXT_BLOCKED,
        ),
        (
            CampaignState.CONTEXT_COLLECTING,
            CampaignEvent.CONTEXT_READY,
            CampaignState.CONTEXT_READY,
        ),
        (
            CampaignState.CONTEXT_BLOCKED,
            CampaignEvent.MESSAGE_RECEIVED,
            CampaignState.CONTEXT_COLLECTING,
        ),
        (
            CampaignState.CONTEXT_READY,
            CampaignEvent.STRATEGY_REQUESTED,
            CampaignState.STRATEGY_GENERATING,
        ),
        (
            CampaignState.STRATEGY_GENERATING,
            CampaignEvent.STRATEGY_CREATED,
            CampaignState.STRATEGY_PENDING_CONFIRMATION,
        ),
        (
            CampaignState.STRATEGY_PENDING_CONFIRMATION,
            CampaignEvent.STRATEGY_REVISION_REQUESTED,
            CampaignState.STRATEGY_GENERATING,
        ),
        (
            CampaignState.STRATEGY_PENDING_CONFIRMATION,
            CampaignEvent.STRATEGY_CONFIRMED,
            CampaignState.STRATEGY_CONFIRMED,
        ),
        (
            CampaignState.STRATEGY_CONFIRMED,
            CampaignEvent.STAGE_EXECUTION_STARTED,
            CampaignState.STAGE_EXECUTION,
        ),
    ],
)
def test_transition_accepts_first_slice_paths(current, event, expected) -> None:
    assert transition(current, event) is expected


def test_transition_rejects_skipping_business_steps() -> None:
    with pytest.raises(InvalidTransitionError):
        transition(CampaignState.DRAFT, CampaignEvent.STRATEGY_CREATED)


@pytest.mark.parametrize(
    ("current", "event", "expected"),
    [
        (
            StageWorkflowState.NOT_STARTED,
            StageWorkflowEvent.MEDIA_PLAN_REQUESTED,
            StageWorkflowState.MEDIA_PLAN_GENERATING,
        ),
        (
            StageWorkflowState.MEDIA_PLAN_GENERATING,
            StageWorkflowEvent.MEDIA_PLAN_CREATED,
            StageWorkflowState.MEDIA_PLAN_PENDING_CONFIRMATION,
        ),
        (
            StageWorkflowState.MEDIA_PLAN_PENDING_CONFIRMATION,
            StageWorkflowEvent.MEDIA_PLAN_REVISION_REQUESTED,
            StageWorkflowState.MEDIA_PLAN_GENERATING,
        ),
        (
            StageWorkflowState.MEDIA_PLAN_PENDING_CONFIRMATION,
            StageWorkflowEvent.MEDIA_PLAN_CONFIRMED,
            StageWorkflowState.MEDIA_PLAN_CONFIRMED,
        ),
        (
            StageWorkflowState.MEDIA_PLAN_CONFIRMED,
            StageWorkflowEvent.VIDEO_SCRIPTS_REQUESTED,
            StageWorkflowState.VIDEO_SCRIPTS_GENERATING,
        ),
        (
            StageWorkflowState.VIDEO_SCRIPTS_GENERATING,
            StageWorkflowEvent.VIDEO_SCRIPTS_PARTIALLY_FAILED,
            StageWorkflowState.VIDEO_SCRIPTS_PARTIAL_FAILURE,
        ),
        (
            StageWorkflowState.VIDEO_SCRIPTS_PARTIAL_FAILURE,
            StageWorkflowEvent.VIDEO_SCRIPTS_RETRY_REQUESTED,
            StageWorkflowState.VIDEO_SCRIPTS_GENERATING,
        ),
        (
            StageWorkflowState.VIDEO_SCRIPTS_GENERATING,
            StageWorkflowEvent.VIDEO_SCRIPTS_CREATED,
            StageWorkflowState.VIDEO_SCRIPTS_PENDING_CONFIRMATION,
        ),
        (
            StageWorkflowState.VIDEO_SCRIPTS_PENDING_CONFIRMATION,
            StageWorkflowEvent.VIDEO_SCRIPTS_CONFIRMED,
            StageWorkflowState.VIDEO_SCRIPTS_CONFIRMED,
        ),
    ],
)
def test_stage_transition_accepts_media_plan_paths(current, event, expected) -> None:
    assert transition_stage(current, event) is expected
