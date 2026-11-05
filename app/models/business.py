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
        UniqueConstraint(
            "framework_id", "version", name="uq_strategy_framework_version"
        ),
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

