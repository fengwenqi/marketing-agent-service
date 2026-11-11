import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

json_type = JSON().with_variant(JSONB(), "postgresql")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Campaign(TimestampMixin, Base):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(64), index=True)
    context: Mapped[dict[str, Any] | None] = mapped_column(json_type)
    version: Mapped[int] = mapped_column(default=1)
    # Gateway 的 runtime session_id 只在当前进程有效；这里只保存可跨重启恢复的 ID。
    hermes_stored_session_id: Mapped[str | None] = mapped_column(String(128))


class ConversationMessage(TimestampMixin, Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint("campaign_id", "idempotency_key"),
        UniqueConstraint("campaign_id", "sequence", name="uq_message_campaign_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), index=True)
    # 时间戳相同时仍按活动内序号稳定排序。
    sequence: Mapped[int] = mapped_column(nullable=False)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(json_type)
    idempotency_key: Mapped[str] = mapped_column(String(128))


class GateDecision(TimestampMixin, Base):
    __tablename__ = "gate_decisions"
    __table_args__ = (UniqueConstraint("campaign_id", "gate_code", "decision_version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), index=True)
    gate_code: Mapped[str] = mapped_column(String(16))
    decision: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[str] = mapped_column(String(100))
    decision_version: Mapped[int] = mapped_column(default=1)
    # Gate 可审核不同领域对象；不设置通用外键，由应用层验证资源归属。
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[uuid.UUID | None] = mapped_column()
    resource_version_id: Mapped[uuid.UUID | None] = mapped_column()


class MarketingStrategyFramework(TimestampMixin, Base):
    __tablename__ = "marketing_strategy_frameworks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "marketing_strategy_versions.id",
            name="fk_strategy_framework_current_version",
            use_alter=True,
        )
    )
    confirmed_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "marketing_strategy_versions.id",
            name="fk_strategy_framework_confirmed_version",
            use_alter=True,
        )
    )
    hermes_stored_session_id: Mapped[str | None] = mapped_column(String(128))


class MarketingStrategyVersion(TimestampMixin, Base):
    __tablename__ = "marketing_strategy_versions"
    __table_args__ = (
        UniqueConstraint("framework_id", "version", name="uq_strategy_framework_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    framework_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("marketing_strategy_frameworks.id"), index=True
    )
    version: Mapped[int] = mapped_column(nullable=False)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("marketing_strategy_versions.id")
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    revision_instruction: Mapped[str | None] = mapped_column(Text)
    content: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)


class CampaignStageWorkflow(TimestampMixin, Base):
    __tablename__ = "campaign_stage_workflows"
    __table_args__ = (
        UniqueConstraint("campaign_id", "stage_code", name="uq_stage_workflow_campaign_stage"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), index=True)
    stage_code: Mapped[str] = mapped_column(String(1))
    state: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class StageMediaPlan(TimestampMixin, Base):
    __tablename__ = "stage_media_plans"
    __table_args__ = (
        UniqueConstraint("campaign_id", "stage_code", name="uq_media_plan_campaign_stage"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), index=True)
    stage_workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaign_stage_workflows.id"), unique=True
    )
    stage_code: Mapped[str] = mapped_column(String(1))
    source_strategy_framework_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("marketing_strategy_frameworks.id")
    )
    source_strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("marketing_strategy_versions.id")
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "stage_media_plan_versions.id",
            name="fk_media_plan_current_version",
            use_alter=True,
        )
    )
    confirmed_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "stage_media_plan_versions.id",
            name="fk_media_plan_confirmed_version",
            use_alter=True,
        )
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    hermes_stored_session_id: Mapped[str | None] = mapped_column(String(128))


class StageMediaPlanVersion(TimestampMixin, Base):
    __tablename__ = "stage_media_plan_versions"
    __table_args__ = (UniqueConstraint("media_plan_id", "version", name="uq_media_plan_version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    media_plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stage_media_plans.id"), index=True)
    version: Mapped[int] = mapped_column(nullable=False)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stage_media_plan_versions.id")
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    revision_instruction: Mapped[str | None] = mapped_column(Text)
    confirmation_inputs: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    missing_confirmation_fields: Mapped[list[str]] = mapped_column(json_type, nullable=False)
    validation_result: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)


class VideoScriptBatch(TimestampMixin, Base):
    __tablename__ = "video_script_batches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), index=True)
    stage_workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaign_stage_workflows.id"), index=True
    )
    source_media_plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stage_media_plans.id"), index=True
    )
    source_media_plan_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stage_media_plan_versions.id"), unique=True
    )
    stage_code: Mapped[str] = mapped_column(String(1))
    status: Mapped[str] = mapped_column(String(32), index=True)
    total_count: Mapped[int] = mapped_column(default=15)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VideoScriptJob(TimestampMixin, Base):
    __tablename__ = "video_script_jobs"
    __table_args__ = (
        UniqueConstraint("batch_id", "creative_slot_id", name="uq_script_job_batch_slot"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("video_script_batches.id"), index=True)
    creative_slot_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32), index=True)
    attempt_count: Mapped[int] = mapped_column(default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VideoScript(TimestampMixin, Base):
    __tablename__ = "video_scripts"
    __table_args__ = (
        UniqueConstraint("batch_id", "creative_slot_id", name="uq_video_script_batch_slot"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), index=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("video_script_batches.id"), index=True)
    creative_slot_id: Mapped[str] = mapped_column(String(100))
    source_media_plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stage_media_plans.id"))
    source_media_plan_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stage_media_plan_versions.id")
    )
    stage_code: Mapped[str] = mapped_column(String(1))
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "video_script_versions.id", name="fk_video_script_current_version", use_alter=True
        )
    )
    confirmed_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "video_script_versions.id", name="fk_video_script_confirmed_version", use_alter=True
        )
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    hermes_stored_session_id: Mapped[str | None] = mapped_column(String(128))


class VideoScriptVersion(TimestampMixin, Base):
    __tablename__ = "video_script_versions"
    __table_args__ = (
        UniqueConstraint("video_script_id", "version", name="uq_video_script_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    video_script_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("video_scripts.id"), index=True)
    version: Mapped[int] = mapped_column(nullable=False)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("video_script_versions.id")
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    revision_instruction: Mapped[str | None] = mapped_column(Text)
    validation_result: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)


class VideoScriptConfirmationSet(TimestampMixin, Base):
    __tablename__ = "video_script_confirmation_sets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("video_script_batches.id"), unique=True)
    manifest: Mapped[list[dict[str, Any]]] = mapped_column(json_type, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)


class HermesExecution(TimestampMixin, Base):
    __tablename__ = "hermes_executions"
    __table_args__ = (UniqueConstraint("request_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    skill: Mapped[str] = mapped_column(String(100))
    hermes_session_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    input_payload: Mapped[dict[str, Any]] = mapped_column(json_type)
    output_payload: Mapped[dict[str, Any] | None] = mapped_column(json_type)
    error: Mapped[str | None] = mapped_column(Text)


class StateTransition(TimestampMixin, Base):
    __tablename__ = "state_transitions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), index=True)
    from_state: Mapped[str] = mapped_column(String(64))
    to_state: Mapped[str] = mapped_column(String(64))
    event: Mapped[str] = mapped_column(String(64))


class IdempotencyRecord(TimestampMixin, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("scope", "idempotency_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(String(100))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    response_payload: Mapped[dict[str, Any]] = mapped_column(json_type)
