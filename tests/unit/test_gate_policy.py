import pytest

from app.campaigns.domain import (
    CampaignState,
    GateCode,
    GateDecisionType,
    StageWorkflowState,
)
from app.gates.policy import GateNotAvailableError, validate_gate_decision


def test_gate_1_is_available_when_strategy_waits_for_confirmation() -> None:
    validate_gate_decision(
        CampaignState.STRATEGY_PENDING_CONFIRMATION,
        GateCode.GATE_1,
        GateDecisionType.APPROVE,
    )


@pytest.mark.parametrize("gate", [GateCode.GATE_1_5, GateCode.GATE_2, GateCode.GATE_3])
def test_later_gates_are_not_reachable_in_first_slice(gate: GateCode) -> None:
    with pytest.raises(GateNotAvailableError):
        validate_gate_decision(
            CampaignState.STRATEGY_PENDING_CONFIRMATION,
            gate,
            GateDecisionType.APPROVE,
        )


def test_gate_1_5_is_available_for_pending_stage_media_plan() -> None:
    validate_gate_decision(
        StageWorkflowState.MEDIA_PLAN_PENDING_CONFIRMATION,
        GateCode.GATE_1_5,
        GateDecisionType.APPROVE,
    )


def test_gate_1_5_rejects_unfinished_stage_media_plan() -> None:
    with pytest.raises(GateNotAvailableError):
        validate_gate_decision(
            StageWorkflowState.MEDIA_PLAN_GENERATING,
            GateCode.GATE_1_5,
            GateDecisionType.APPROVE,
        )


def test_gate_2_is_available_for_complete_script_batch() -> None:
    validate_gate_decision(
        StageWorkflowState.VIDEO_SCRIPTS_PENDING_CONFIRMATION,
        GateCode.GATE_2,
        GateDecisionType.APPROVE,
    )


@pytest.mark.parametrize(
    "state",
    [
        StageWorkflowState.VIDEO_SCRIPTS_GENERATING,
        StageWorkflowState.VIDEO_SCRIPTS_PARTIAL_FAILURE,
    ],
)
def test_gate_2_rejects_unfinished_script_batch(state: StageWorkflowState) -> None:
    with pytest.raises(GateNotAvailableError):
        validate_gate_decision(state, GateCode.GATE_2, GateDecisionType.APPROVE)
