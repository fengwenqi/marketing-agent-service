from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.strategies.service import StrategySnapshot


class StrategyReviseRequest(BaseModel):
    expected_version: int = Field(ge=1)
    instruction: str = Field(min_length=1, max_length=10_000)
    actor_id: str = Field(min_length=1, max_length=100)


class StrategyConfirmRequest(BaseModel):
    expected_version: int = Field(ge=1)
    actor_id: str = Field(min_length=1, max_length=100)


class StrategyVersionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    parent_version_id: UUID | None
    status: str
    revision_instruction: str | None
    content: dict
    created_at: datetime


class GateDecisionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    gate_code: str
    decision: str
    reason: str | None
    actor_id: str
    decision_version: int
    resource_type: str | None
    resource_id: UUID | None
    resource_version_id: UUID | None
    created_at: datetime


class StrategyFrameworkView(BaseModel):
    campaign_id: UUID
    campaign_state: str
    framework_id: UUID
    status: str
    current_version_id: UUID
    confirmed_version_id: UUID | None
    versions: list[StrategyVersionView]
    gate_decisions: list[GateDecisionView]

    @classmethod
    def from_snapshot(cls, snapshot: StrategySnapshot) -> "StrategyFrameworkView":
        current = snapshot.current_version
        return cls(
            campaign_id=snapshot.campaign.id,
            campaign_state=snapshot.campaign.state,
            framework_id=snapshot.framework.id,
            status=snapshot.framework.status,
            current_version_id=current.id,
            confirmed_version_id=snapshot.framework.confirmed_version_id,
            versions=[StrategyVersionView.model_validate(item) for item in snapshot.versions],
            gate_decisions=[
                GateDecisionView.model_validate(item) for item in snapshot.decisions
            ],
        )
