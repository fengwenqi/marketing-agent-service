import pytest

from app.campaigns.domain import CampaignEvent, CampaignState
from app.campaigns.state_machine import InvalidTransitionError, transition


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
    ],
)
def test_transition_accepts_first_slice_paths(current, event, expected) -> None:
    assert transition(current, event) is expected


def test_transition_rejects_skipping_business_steps() -> None:
    with pytest.raises(InvalidTransitionError):
        transition(CampaignState.DRAFT, CampaignEvent.STRATEGY_CREATED)

