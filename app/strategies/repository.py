from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import (
    Campaign,
    GateDecision,
    HermesExecution,
    IdempotencyRecord,
    MarketingStrategyFramework,
    MarketingStrategyVersion,
    StateTransition,
)


class StrategyRepository:
    """策略聚合的 PostgreSQL 访问层；事务边界由 StrategyService 控制。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_campaign(self, campaign_id: UUID) -> Campaign | None:
        return await self.session.scalar(
            select(Campaign).where(Campaign.id == campaign_id).with_for_update()
        )

    async def get_framework(
        self, campaign_id: UUID, *, lock: bool = False
    ) -> MarketingStrategyFramework | None:
        statement = select(MarketingStrategyFramework).where(
            MarketingStrategyFramework.campaign_id == campaign_id
        )
        if lock:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    def add_framework(self, framework: MarketingStrategyFramework) -> None:
        self.session.add(framework)

    def add_version(self, version: MarketingStrategyVersion) -> None:
        self.session.add(version)

    async def get_version(self, version_id: UUID) -> MarketingStrategyVersion | None:
        return await self.session.get(MarketingStrategyVersion, version_id)

    async def list_versions(self, framework_id: UUID) -> list[MarketingStrategyVersion]:
        rows = await self.session.scalars(
            select(MarketingStrategyVersion)
            .where(MarketingStrategyVersion.framework_id == framework_id)
            .order_by(MarketingStrategyVersion.version)
        )
        return list(rows.all())

    async def next_version_number(self, framework_id: UUID) -> int:
        maximum = await self.session.scalar(
            select(func.max(MarketingStrategyVersion.version)).where(
                MarketingStrategyVersion.framework_id == framework_id
            )
        )
        return (maximum or 0) + 1

    async def list_decisions(self, campaign_id: UUID) -> list[GateDecision]:
        rows = await self.session.scalars(
            select(GateDecision)
            .where(GateDecision.campaign_id == campaign_id)
            .order_by(GateDecision.decision_version)
        )
        return list(rows.all())

    async def add_gate_decision(
        self,
        *,
        campaign_id: UUID,
        decision: str,
        reason: str | None,
        actor_id: str,
        resource_id: UUID,
        resource_version_id: UUID,
    ) -> GateDecision:
        maximum = await self.session.scalar(
            select(func.max(GateDecision.decision_version)).where(
                GateDecision.campaign_id == campaign_id,
                GateDecision.gate_code == "GATE_1",
            )
        )
        row = GateDecision(
            campaign_id=campaign_id,
            gate_code="GATE_1",
            decision=decision,
            reason=reason,
            actor_id=actor_id,
            decision_version=(maximum or 0) + 1,
            resource_type="MARKETING_STRATEGY",
            resource_id=resource_id,
            resource_version_id=resource_version_id,
        )
        self.session.add(row)
        return row

    async def find_revision_decision(
        self,
        *,
        campaign_id: UUID,
        resource_version_id: UUID,
        reason: str,
        actor_id: str,
    ) -> GateDecision | None:
        return await self.session.scalar(
            select(GateDecision).where(
                GateDecision.campaign_id == campaign_id,
                GateDecision.gate_code == "GATE_1",
                GateDecision.decision == "REVISION_REQUESTED",
                GateDecision.resource_version_id == resource_version_id,
                GateDecision.reason == reason,
                GateDecision.actor_id == actor_id,
            )
        )

    def add_transition(
        self, *, campaign_id: UUID, from_state: str, to_state: str, event: str
    ) -> None:
        self.session.add(
            StateTransition(
                campaign_id=campaign_id,
                from_state=from_state,
                to_state=to_state,
                event=event,
            )
        )

    async def get_idempotency(self, scope: str, key: str) -> dict[str, Any] | None:
        record = await self.session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.idempotency_key == key,
            )
        )
        return record.response_payload if record else None

    def save_idempotency(self, *, scope: str, key: str, response: dict[str, Any]) -> None:
        self.session.add(
            IdempotencyRecord(
                scope=scope,
                idempotency_key=key,
                response_payload=response,
            )
        )

    async def next_execution_attempt(self, prefix: str) -> int:
        count = await self.session.scalar(
            select(func.count(HermesExecution.id)).where(
                HermesExecution.request_id.like(f"{prefix}:attempt-%")
            )
        )
        return int(count or 0) + 1

    async def latest_execution(self, prefix: str) -> HermesExecution | None:
        return await self.session.scalar(
            select(HermesExecution)
            .where(HermesExecution.request_id.like(f"{prefix}:attempt-%"))
            .order_by(HermesExecution.created_at.desc())
            .limit(1)
        )

    def add_execution(
        self,
        *,
        campaign_id: UUID,
        request_id: str,
        stored_session_id: str | None,
        status: str,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        self.session.add(
            HermesExecution(
                campaign_id=campaign_id,
                request_id=request_id,
                skill="marketing-strategy",
                hermes_session_id=stored_session_id,
                status=status,
                input_payload=input_payload,
                output_payload=output_payload,
                error=error,
            )
        )
