from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import (
    Campaign,
    ConversationMessage,
    HermesExecution,
    IdempotencyRecord,
    StateTransition,
)


class CampaignRepository:
    """封装活动持久化；事务的开始、提交和回滚由应用服务控制。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add_campaign(self, campaign: Campaign) -> None:
        self.session.add(campaign)

    async def get_campaign(self, campaign_id: UUID) -> Campaign | None:
        return await self.session.get(Campaign, campaign_id)

    async def lock_campaign(self, campaign_id: UUID) -> Campaign | None:
        statement = select(Campaign).where(Campaign.id == campaign_id).with_for_update()
        return await self.session.scalar(statement)

    async def append_message(
        self,
        *,
        campaign_id: UUID,
        role: str,
        content: str,
        payload: dict[str, Any] | None,
        idempotency_key: str,
    ) -> ConversationMessage:
        # 调用方必须先锁住 campaign 行；这样并发请求不会分配出相同序号。
        maximum = await self.session.scalar(
            select(func.max(ConversationMessage.sequence)).where(
                ConversationMessage.campaign_id == campaign_id
            )
        )
        message = ConversationMessage(
            campaign_id=campaign_id,
            sequence=(maximum or 0) + 1,
            role=role,
            content=content,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        self.session.add(message)
        return message

    async def list_messages(
        self, campaign_id: UUID, *, limit: int | None = None
    ) -> list[ConversationMessage]:
        statement = (
            select(ConversationMessage)
            .where(ConversationMessage.campaign_id == campaign_id)
            .order_by(ConversationMessage.sequence)
        )
        if limit is not None:
            # 先取最近 N 条，再由 Python 恢复正序，避免依赖数据库特有子查询。
            recent = await self.session.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.campaign_id == campaign_id)
                .order_by(ConversationMessage.sequence.desc())
                .limit(limit)
            )
            return list(reversed(recent.all()))
        return list((await self.session.scalars(statement)).all())

    async def get_message_by_idempotency(
        self, campaign_id: UUID, key: str
    ) -> ConversationMessage | None:
        return await self.session.scalar(
            select(ConversationMessage).where(
                ConversationMessage.campaign_id == campaign_id,
                ConversationMessage.idempotency_key == key,
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

    async def save_idempotency(
        self, *, scope: str, key: str, response: dict[str, Any]
    ) -> None:
        self.session.add(
            IdempotencyRecord(
                scope=scope,
                idempotency_key=key,
                response_payload=response,
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

    async def save_execution(
        self,
        *,
        campaign_id: UUID,
        request_id: str,
        skill: str,
        stored_session_id: str | None,
        status: str,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> HermesExecution:
        # request_id 代表一次幂等业务请求。失败后重试时更新原记录，既保留
        # 唯一约束，也让调用方看到这个请求最终的执行结果。
        execution = await self.session.scalar(
            select(HermesExecution).where(HermesExecution.request_id == request_id)
        )
        if execution is None:
            execution = HermesExecution(
                campaign_id=campaign_id,
                request_id=request_id,
            )
            self.session.add(execution)

        execution.skill = skill
        execution.hermes_session_id = stored_session_id
        execution.status = status
        execution.input_payload = input_payload
        execution.output_payload = output_payload
        execution.error = error
        return execution
