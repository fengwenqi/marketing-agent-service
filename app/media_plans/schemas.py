from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.media_plans.service import MediaPlanSnapshot
from app.media_plans.validation import ConfirmationInputs


class MediaPlanGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_inputs: ConfirmationInputs = Field(
        default_factory=ConfirmationInputs
    )


class MediaPlanReviseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    actor_id: str = Field(min_length=1, max_length=100)
    instruction: str | None = Field(default=None, min_length=1, max_length=10_000)
    confirmation_inputs: ConfirmationInputs | None = None

    @model_validator(mode="after")
    def require_revision_change(self) -> "MediaPlanReviseRequest":
        if self.instruction is None and self.confirmation_inputs is None:
            raise ValueError("instruction or confirmation_inputs is required")
        return self


class MediaPlanConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    actor_id: str = Field(min_length=1, max_length=100)


class MediaPlanVersionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    parent_version_id: UUID | None
    status: str
    revision_instruction: str | None
    confirmation_inputs: dict
    missing_confirmation_fields: list[str]
    validation_result: dict
    content: dict
    created_at: datetime


class MediaPlanGateDecisionView(BaseModel):
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


class StageMediaPlanView(BaseModel):
    campaign_id: UUID
    stage_code: str
    workflow_state: str
    plan_id: UUID
    status: str
    source_strategy_version_id: UUID
    current_version_id: UUID
    confirmed_version_id: UUID | None
    current_version: MediaPlanVersionView
    versions: list[MediaPlanVersionView]
    gate_decisions: list[MediaPlanGateDecisionView]

    @classmethod
    def from_snapshot(cls, snapshot: MediaPlanSnapshot) -> "StageMediaPlanView":
        current = snapshot.current_version
        return cls(
            campaign_id=snapshot.plan.campaign_id,
            stage_code=snapshot.plan.stage_code,
            workflow_state=snapshot.workflow.state,
            plan_id=snapshot.plan.id,
            status=snapshot.plan.status,
            source_strategy_version_id=snapshot.plan.source_strategy_version_id,
            current_version_id=current.id,
            confirmed_version_id=snapshot.plan.confirmed_version_id,
            current_version=MediaPlanVersionView.model_validate(current),
            versions=[
                MediaPlanVersionView.model_validate(item) for item in snapshot.versions
            ],
            gate_decisions=[
                MediaPlanGateDecisionView.model_validate(item)
                for item in snapshot.decisions
            ],
        )
