from app.campaigns.domain import CampaignState, GateCode, GateDecisionType


class GateNotAvailableError(ValueError):
    pass


def validate_gate_decision(
    state: CampaignState,
    gate: GateCode,
    decision: GateDecisionType,
) -> None:
    """在服务端验证 Gate，前端隐藏按钮不能替代真正的业务权限检查。"""

    _ = decision
    if state is CampaignState.STRATEGY_PENDING_CONFIRMATION and gate is GateCode.GATE_1:
        return
    raise GateNotAvailableError(f"{gate} is unavailable while campaign is {state}")

