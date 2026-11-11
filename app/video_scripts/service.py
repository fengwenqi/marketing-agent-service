from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from app.campaigns.domain import (
    StageCode,
    StageWorkflowEvent,
    StageWorkflowState,
)
from app.campaigns.state_machine import transition_stage
from app.hermes.contracts import StageMediaPlanResult
from app.models.business import (
    CampaignStageWorkflow,
    VideoScript,
    VideoScriptBatch,
    VideoScriptJob,
)
from app.video_scripts.repository import VideoScriptRepository


class VideoScriptBatchError(RuntimeError):
    pass


class StageNotOpenError(VideoScriptBatchError):
    pass


class VideoScriptBatchNotFoundError(VideoScriptBatchError):
    pass


class MediaPlanNotConfirmedError(VideoScriptBatchError):
    pass


class NoRetryableScriptJobsError(VideoScriptBatchError):
    pass


@dataclass(frozen=True)
class VideoScriptBatchSnapshot:
    batch: VideoScriptBatch
    workflow: CampaignStageWorkflow
    jobs: list[VideoScriptJob]
    scripts: list[VideoScript]
    counts: dict[str, int]


class VideoScriptBatchService:
    def __init__(self, repository: VideoScriptRepository) -> None:
        self.repository = repository

    @staticmethod
    def _require_open(stage_code: StageCode) -> None:
        if stage_code is not StageCode.A:
            raise StageNotOpenError(f"stage {stage_code.value} is not open")

    async def get_batch(self, campaign_id: UUID, stage_code: StageCode) -> VideoScriptBatchSnapshot:
        self._require_open(stage_code)
        workflow = await self.repository.get_workflow(campaign_id, stage_code.value)
        batch = await self.repository.get_batch(campaign_id, stage_code.value)
        if workflow is None or batch is None:
            raise VideoScriptBatchNotFoundError(campaign_id)
        return VideoScriptBatchSnapshot(
            batch=batch,
            workflow=workflow,
            jobs=await self.repository.list_jobs(batch.id),
            scripts=await self.repository.list_scripts(batch.id),
            counts=await self.repository.job_counts(batch.id),
        )

    async def get_batch_by_id(self, batch_id: UUID) -> VideoScriptBatchSnapshot:
        batch = await self.repository.get_batch_by_id(batch_id)
        if batch is None:
            raise VideoScriptBatchNotFoundError(batch_id)
        return await self.get_batch(batch.campaign_id, StageCode(batch.stage_code))

    async def create_or_get(
        self, campaign_id: UUID, stage_code: StageCode, idempotency_key: str
    ) -> VideoScriptBatchSnapshot:
        self._require_open(stage_code)
        scope = f"video-script-batch-create:{campaign_id}:{stage_code.value}"
        cached = await self.repository.get_idempotency(scope, idempotency_key)
        if cached:
            return await self.get_batch(campaign_id, stage_code)

        workflow = await self.repository.get_workflow(campaign_id, stage_code.value, lock=True)
        plan = await self.repository.get_media_plan(campaign_id, stage_code.value, lock=True)
        if plan is not None and plan.confirmed_version_id is not None:
            existing = await self.repository.get_batch_by_source_version(plan.confirmed_version_id)
            if existing is not None:
                return await self.get_batch(campaign_id, stage_code)
        if (
            workflow is None
            or plan is None
            or plan.status != "CONFIRMED"
            or plan.confirmed_version_id is None
            or workflow.state != StageWorkflowState.MEDIA_PLAN_CONFIRMED.value
        ):
            raise MediaPlanNotConfirmedError(campaign_id)
        plan_version = await self.repository.get_media_plan_version(plan.confirmed_version_id)
        if plan_version is None:
            raise MediaPlanNotConfirmedError(plan.confirmed_version_id)
        media_plan = StageMediaPlanResult.model_validate(plan_version.content)
        batch = VideoScriptBatch(
            id=uuid4(),
            campaign_id=campaign_id,
            stage_workflow_id=workflow.id,
            source_media_plan_id=plan.id,
            source_media_plan_version_id=plan_version.id,
            stage_code=stage_code.value,
            status="PENDING",
            total_count=15,
        )
        self.repository.add_batch(batch)
        for slot in media_plan.creative_slots:
            self.repository.add_job(
                VideoScriptJob(
                    id=uuid4(),
                    batch_id=batch.id,
                    creative_slot_id=slot.id,
                    status="PENDING",
                    attempt_count=0,
                )
            )
        workflow.state = transition_stage(
            StageWorkflowState(workflow.state),
            StageWorkflowEvent.VIDEO_SCRIPTS_REQUESTED,
        ).value
        workflow.version += 1
        self.repository.save_idempotency(
            scope,
            idempotency_key,
            {"batch_id": str(batch.id), "source_version_id": str(plan_version.id)},
        )
        try:
            await self.repository.session.commit()
        except IntegrityError:
            await self.repository.session.rollback()
            existing = await self.repository.get_batch_by_source_version(plan_version.id)
            if existing is None:
                raise
        return await self.get_batch(campaign_id, stage_code)

    async def retry_failed(
        self, campaign_id: UUID, stage_code: StageCode, idempotency_key: str
    ) -> VideoScriptBatchSnapshot:
        self._require_open(stage_code)
        scope = f"video-script-batch-retry:{campaign_id}:{stage_code.value}"
        if await self.repository.get_idempotency(scope, idempotency_key):
            return await self.get_batch(campaign_id, stage_code)
        workflow = await self.repository.get_workflow(campaign_id, stage_code.value, lock=True)
        batch = await self.repository.get_batch(campaign_id, stage_code.value, lock=True)
        if workflow is None or batch is None:
            raise VideoScriptBatchNotFoundError(campaign_id)
        failed = await self.repository.list_jobs_with_status(batch.id, {"FAILED_RETRYABLE"})
        if not failed:
            raise NoRetryableScriptJobsError(batch.id)
        for job in failed:
            job.status = "PENDING"
            job.last_error_code = None
            job.last_error_message = None
            job.started_at = None
            job.finished_at = None
        batch.status = "RUNNING"
        workflow.state = transition_stage(
            StageWorkflowState(workflow.state),
            StageWorkflowEvent.VIDEO_SCRIPTS_RETRY_REQUESTED,
        ).value
        workflow.version += 1
        self.repository.save_idempotency(scope, idempotency_key, {"batch_id": str(batch.id)})
        await self.repository.session.commit()
        return await self.get_batch(campaign_id, stage_code)
