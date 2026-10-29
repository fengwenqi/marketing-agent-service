import pytest

from app.campaigns.domain import CampaignState, GateCode, GateDecisionType
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

