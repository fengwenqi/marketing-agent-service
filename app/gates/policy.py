from app.campaigns.domain import (
    CampaignState,
    GateCode,
    GateDecisionType,
    StageWorkflowState,
)


class GateNotAvailableError(ValueError):
    pass


def validate_gate_decision(
    state: CampaignState | StageWorkflowState,
    gate: GateCode,
    decision: GateDecisionType,
) -> None:
    """在服务端验证 Gate，前端隐藏按钮不能替代真正的业务权限检查。"""

    _ = decision
    if state is CampaignState.STRATEGY_PENDING_CONFIRMATION and gate is GateCode.GATE_1:
        return
    if (
        state is StageWorkflowState.MEDIA_PLAN_PENDING_CONFIRMATION
        and gate is GateCode.GATE_1_5
    ):
        return
    raise GateNotAvailableError(f"{gate} is unavailable while state is {state}")

