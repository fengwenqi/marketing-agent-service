from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import (
    Campaign,
    CampaignStageWorkflow,
    HermesExecution,
    IdempotencyRecord,
    MarketingStrategyVersion,
    StageMediaPlan,
    StageMediaPlanVersion,
    VideoScript,
    VideoScriptBatch,
    VideoScriptJob,
    VideoScriptVersion,
)


class VideoScriptRepository:
    """视频脚本批次领域的 PostgreSQL 访问层；事务由服务或执行器控制。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_campaign(self, campaign_id: UUID) -> Campaign | None:
        return await self.session.get(Campaign, campaign_id)

    async def get_workflow(
        self, campaign_id: UUID, stage_code: str, *, lock: bool = False
    ) -> CampaignStageWorkflow | None:
        statement = select(CampaignStageWorkflow).where(
            CampaignStageWorkflow.campaign_id == campaign_id,
            CampaignStageWorkflow.stage_code == stage_code,
        )
        if lock:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_media_plan(
        self, campaign_id: UUID, stage_code: str, *, lock: bool = False
    ) -> StageMediaPlan | None:
        statement = select(StageMediaPlan).where(
            StageMediaPlan.campaign_id == campaign_id,
            StageMediaPlan.stage_code == stage_code,
        )
        if lock:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_media_plan_version(self, version_id: UUID) -> StageMediaPlanVersion | None:
        return await self.session.get(StageMediaPlanVersion, version_id)

    async def get_strategy_version(self, version_id: UUID) -> MarketingStrategyVersion | None:
        return await self.session.get(MarketingStrategyVersion, version_id)

    async def get_batch(
        self, campaign_id: UUID, stage_code: str, *, lock: bool = False
    ) -> VideoScriptBatch | None:
        statement = select(VideoScriptBatch).where(
            VideoScriptBatch.campaign_id == campaign_id,
            VideoScriptBatch.stage_code == stage_code,
        )
        if lock:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_batch_by_id(
        self, batch_id: UUID, *, lock: bool = False
    ) -> VideoScriptBatch | None:
        statement = select(VideoScriptBatch).where(VideoScriptBatch.id == batch_id)
        if lock:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_batch_by_source_version(self, source_version_id: UUID) -> VideoScriptBatch | None:
        return await self.session.scalar(
            select(VideoScriptBatch).where(
                VideoScriptBatch.source_media_plan_version_id == source_version_id
            )
        )

    def add_batch(self, batch: VideoScriptBatch) -> None:
        self.session.add(batch)

    def add_job(self, job: VideoScriptJob) -> None:
        self.session.add(job)

    def add_script(self, script: VideoScript) -> None:
        self.session.add(script)

    def add_version(self, version: VideoScriptVersion) -> None:
        self.session.add(version)

    async def list_jobs(self, batch_id: UUID) -> list[VideoScriptJob]:
        rows = await self.session.scalars(
            select(VideoScriptJob)
            .where(VideoScriptJob.batch_id == batch_id)
            .order_by(VideoScriptJob.created_at, VideoScriptJob.creative_slot_id)
        )
        return list(rows)

    async def list_jobs_with_status(
        self, batch_id: UUID, statuses: set[str]
    ) -> list[VideoScriptJob]:
        rows = await self.session.scalars(
            select(VideoScriptJob)
            .where(
                VideoScriptJob.batch_id == batch_id,
                VideoScriptJob.status.in_(statuses),
            )
            .order_by(VideoScriptJob.created_at, VideoScriptJob.creative_slot_id)
        )
        return list(rows)

    async def lock_job(self, job_id: UUID) -> VideoScriptJob | None:
        return await self.session.scalar(
            select(VideoScriptJob).where(VideoScriptJob.id == job_id).with_for_update()
        )

    async def list_running_jobs(self) -> list[VideoScriptJob]:
        rows = await self.session.scalars(
            select(VideoScriptJob)
            .where(VideoScriptJob.status == "RUNNING")
            .with_for_update(skip_locked=True)
        )
        return list(rows)

    async def job_counts(self, batch_id: UUID) -> dict[str, int]:
        rows = await self.session.execute(
            select(VideoScriptJob.status, func.count())
            .where(VideoScriptJob.batch_id == batch_id)
            .group_by(VideoScriptJob.status)
        )
        counts = Counter({status: count for status, count in rows})
        return {
            "pending": counts["PENDING"],
            "running": counts["RUNNING"],
            "succeeded": counts["SUCCEEDED"],
            "failed": counts["FAILED_RETRYABLE"],
        }

    async def list_scripts(self, batch_id: UUID) -> list[VideoScript]:
        rows = await self.session.scalars(
            select(VideoScript)
            .where(VideoScript.batch_id == batch_id)
            .order_by(VideoScript.creative_slot_id)
        )
        return list(rows)

    async def get_script_for_slot(
        self, batch_id: UUID, creative_slot_id: str, *, lock: bool = False
    ) -> VideoScript | None:
        statement = select(VideoScript).where(
            VideoScript.batch_id == batch_id,
            VideoScript.creative_slot_id == creative_slot_id,
        )
        if lock:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_idempotency(self, scope: str, key: str) -> dict[str, Any] | None:
        row = await self.session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.idempotency_key == key,
            )
        )
        return row.response_payload if row else None

    def save_idempotency(self, scope: str, key: str, response: dict[str, Any]) -> None:
        self.session.add(
            IdempotencyRecord(
                scope=scope,
                idempotency_key=key,
                response_payload=response,
            )
        )

    def add_execution(
        self,
        *,
        campaign_id: UUID,
        request_id: str,
        skill: str = "marketing-video-script",
        stored_session_id: str | None,
        status: str,
        input_payload: dict,
        output_payload: dict | None = None,
        error: str | None = None,
    ) -> None:
        self.session.add(
            HermesExecution(
                campaign_id=campaign_id,
                request_id=request_id,
                skill=skill,
                hermes_session_id=stored_session_id,
                status=status,
                input_payload=input_payload,
                output_payload=output_payload,
                error=error,
            )
        )

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(UTC)
