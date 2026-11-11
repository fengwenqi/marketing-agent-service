from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.video_scripts.service import (
    ExpectedScriptVersion,
    VideoScriptBatchSnapshot,
    VideoScriptSnapshot,
)


class VideoScriptReviseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    instruction: str = Field(min_length=1, max_length=10_000)
    actor_id: str = Field(min_length=1, max_length=100)


class ExpectedScriptVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_id: UUID
    version_id: UUID

    def to_domain(self) -> ExpectedScriptVersion:
        return ExpectedScriptVersion(
            script_id=self.script_id,
            version_id=self.version_id,
        )


class VideoScriptBatchConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(min_length=1, max_length=100)
    expected_script_versions: list[ExpectedScriptVersionRequest] = Field(
        min_length=15,
        max_length=15,
    )


class VideoScriptJobView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    creative_slot_id: str
    status: str
    attempt_count: int
    last_error_code: str | None
    last_error_message: str | None


class VideoScriptVersionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    parent_version_id: UUID | None
    status: str
    revision_instruction: str | None
    validation_result: dict
    content: dict
    created_at: datetime


class VideoScriptSummaryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    creative_slot_id: str
    status: str
    current_version_id: UUID | None
    confirmed_version_id: UUID | None


class VideoScriptView(BaseModel):
    id: UUID
    campaign_id: UUID
    batch_id: UUID
    stage_code: str
    creative_slot_id: str
    status: str
    current_version_id: UUID
    confirmed_version_id: UUID | None
    current_version: VideoScriptVersionView
    versions: list[VideoScriptVersionView]

    @classmethod
    def from_snapshot(cls, snapshot: VideoScriptSnapshot) -> "VideoScriptView":
        return cls(
            id=snapshot.script.id,
            campaign_id=snapshot.script.campaign_id,
            batch_id=snapshot.script.batch_id,
            stage_code=snapshot.script.stage_code,
            creative_slot_id=snapshot.script.creative_slot_id,
            status=snapshot.script.status,
            current_version_id=snapshot.current_version.id,
            confirmed_version_id=snapshot.script.confirmed_version_id,
            current_version=VideoScriptVersionView.model_validate(snapshot.current_version),
            versions=[
                VideoScriptVersionView.model_validate(version) for version in snapshot.versions
            ],
        )


class VideoScriptBatchView(BaseModel):
    batch_id: UUID
    campaign_id: UUID
    stage_code: str
    status: str
    workflow_state: str
    total_count: int
    counts: dict[str, int]
    jobs: list[VideoScriptJobView]
    scripts: list[VideoScriptSummaryView]
    confirmation_set_id: UUID | None

    @classmethod
    def from_snapshot(cls, snapshot: VideoScriptBatchSnapshot) -> "VideoScriptBatchView":
        return cls(
            batch_id=snapshot.batch.id,
            campaign_id=snapshot.batch.campaign_id,
            stage_code=snapshot.batch.stage_code,
            status=snapshot.batch.status,
            workflow_state=snapshot.workflow.state,
            total_count=snapshot.batch.total_count,
            counts=snapshot.counts,
            jobs=[VideoScriptJobView.model_validate(job) for job in snapshot.jobs],
            scripts=[VideoScriptSummaryView.model_validate(script) for script in snapshot.scripts],
            confirmation_set_id=(
                snapshot.confirmation_set.id if snapshot.confirmation_set else None
            ),
        )
