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


class ConversationMessage(TimestampMixin, Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (UniqueConstraint("campaign_id", "idempotency_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), index=True)
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
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[str] = mapped_column(String(100))
    decision_version: Mapped[int] = mapped_column(default=1)


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

