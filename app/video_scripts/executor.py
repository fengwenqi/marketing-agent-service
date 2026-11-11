import asyncio
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.campaigns.domain import StageWorkflowEvent, StageWorkflowState
from app.campaigns.state_machine import transition_stage
from app.hermes.ports import HermesPort, VideoScriptGenerationRequest
from app.models.business import VideoScript, VideoScriptVersion
from app.video_scripts.repository import VideoScriptRepository
from app.video_scripts.service import VideoScriptBatchService, VideoScriptBatchSnapshot
from app.video_scripts.validation import validate_video_script


class VideoScriptBatchExecutor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        hermes: HermesPort,
        max_concurrency: int,
    ) -> None:
        self.session_factory = session_factory
        self.hermes = hermes
        self.max_concurrency = max_concurrency
        self._tasks: set[asyncio.Task[None]] = set()

    def schedule(self, batch_id: UUID) -> None:
        task = asyncio.create_task(self._scheduled_run(batch_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _scheduled_run(self, batch_id: UUID) -> None:
        await self.run_batch(batch_id)

    async def run_batch(self, batch_id: UUID) -> VideoScriptBatchSnapshot:
        async with self.session_factory() as session:
            repository = VideoScriptRepository(session)
            batch = await repository.get_batch_by_id(batch_id, lock=True)
            if batch is None:
                raise LookupError(batch_id)
            batch.status = "RUNNING"
            jobs = await repository.list_jobs_with_status(batch_id, {"PENDING"})
            await session.commit()

        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def run(job_id: UUID) -> None:
            async with semaphore:
                await self._run_job(job_id)

        await asyncio.gather(*(run(job.id) for job in jobs))
        await self._refresh_batch(batch_id)
        async with self.session_factory() as session:
            return await VideoScriptBatchService(VideoScriptRepository(session)).get_batch_by_id(
                batch_id
            )

    async def _run_job(self, job_id: UUID) -> None:
        async with self.session_factory() as session:
            repository = VideoScriptRepository(session)
            job = await repository.lock_job(job_id)
            if job is None or job.status != "PENDING":
                return
            batch = await repository.get_batch_by_id(job.batch_id)
            if batch is None:
                return
            campaign = await repository.get_campaign(batch.campaign_id)
            plan = await repository.get_media_plan(batch.campaign_id, batch.stage_code)
            plan_version = await repository.get_media_plan_version(
                batch.source_media_plan_version_id
            )
            if campaign is None or plan is None or plan_version is None:
                return
            strategy_version = await repository.get_strategy_version(
                plan.source_strategy_version_id
            )
            if strategy_version is None:
                return
            plan_content = plan_version.content
            slot = next(
                item
                for item in plan_content["creative_slots"]
                if item["id"] == job.creative_slot_id
            )
            job.status = "RUNNING"
            job.attempt_count += 1
            job.started_at = repository.utcnow()
            attempt = job.attempt_count
            request_id = f"video-script:{job.id}:attempt-{attempt}"
            await session.commit()

        request = VideoScriptGenerationRequest(
            campaign_id=str(batch.campaign_id),
            stage_code=batch.stage_code,
            request_id=request_id,
            marketing_context=campaign.context or {},
            confirmed_strategy=strategy_version.content,
            confirmed_media_plan=plan_content,
            creative_slot=slot,
        )
        try:
            response = await self.hermes.run_video_script_generation(request)
            validation = validate_video_script(response.result, slot)
            if not validation.valid:
                raise ValueError(validation.model_dump_json())
        except Exception as error:
            async with self.session_factory() as session:
                repository = VideoScriptRepository(session)
                job = await repository.lock_job(job_id)
                if job is not None:
                    job.status = "FAILED_RETRYABLE"
                    job.last_error_code = "SCRIPT_GENERATION_FAILED"
                    job.last_error_message = str(error)
                    job.finished_at = repository.utcnow()
                    repository.add_execution(
                        campaign_id=batch.campaign_id,
                        request_id=request_id,
                        stored_session_id=None,
                        status="FAILED",
                        input_payload={"creative_slot": slot},
                        error=str(error),
                    )
                    await session.commit()
            await self._refresh_batch(batch.id)
            return

        async with self.session_factory() as session:
            repository = VideoScriptRepository(session)
            job = await repository.lock_job(job_id)
            if job is None or job.status != "RUNNING":
                return
            script = await repository.get_script_for_slot(batch.id, job.creative_slot_id, lock=True)
            if script is None:
                script = VideoScript(
                    id=uuid4(),
                    campaign_id=batch.campaign_id,
                    batch_id=batch.id,
                    creative_slot_id=job.creative_slot_id,
                    source_media_plan_id=batch.source_media_plan_id,
                    source_media_plan_version_id=batch.source_media_plan_version_id,
                    stage_code=batch.stage_code,
                    status="PENDING_CONFIRMATION",
                    hermes_stored_session_id=response.stored_session_id,
                )
                repository.add_script(script)
                await session.flush()
            version = VideoScriptVersion(
                id=uuid4(),
                video_script_id=script.id,
                version=1,
                status="PENDING_CONFIRMATION",
                validation_result=validation.model_dump(mode="json"),
                content=response.result.model_dump(mode="json"),
            )
            repository.add_version(version)
            await session.flush()
            script.current_version_id = version.id
            script.hermes_stored_session_id = response.stored_session_id
            job.status = "SUCCEEDED"
            job.finished_at = repository.utcnow()
            repository.add_execution(
                campaign_id=batch.campaign_id,
                request_id=request_id,
                stored_session_id=response.stored_session_id,
                status="SUCCEEDED",
                input_payload={"creative_slot": slot},
                output_payload=version.content,
            )
            await session.commit()
        await self._refresh_batch(batch.id)

    async def _refresh_batch(self, batch_id: UUID) -> None:
        async with self.session_factory() as session:
            repository = VideoScriptRepository(session)
            batch = await repository.get_batch_by_id(batch_id, lock=True)
            if batch is None:
                return
            workflow = await repository.get_workflow(batch.campaign_id, batch.stage_code, lock=True)
            if workflow is None:
                return
            counts = await repository.job_counts(batch_id)
            if counts["succeeded"] == batch.total_count:
                batch.status = "COMPLETED"
                batch.completed_at = repository.utcnow()
                if workflow.state == StageWorkflowState.VIDEO_SCRIPTS_GENERATING.value:
                    workflow.state = transition_stage(
                        StageWorkflowState(workflow.state),
                        StageWorkflowEvent.VIDEO_SCRIPTS_CREATED,
                    ).value
                    workflow.version += 1
            elif counts["failed"] and not counts["running"]:
                batch.status = "PARTIAL_FAILURE"
                if workflow.state == StageWorkflowState.VIDEO_SCRIPTS_GENERATING.value:
                    workflow.state = transition_stage(
                        StageWorkflowState(workflow.state),
                        StageWorkflowEvent.VIDEO_SCRIPTS_PARTIALLY_FAILED,
                    ).value
                    workflow.version += 1
            else:
                batch.status = "RUNNING"
            await session.commit()

    async def recover_interrupted(self) -> int:
        batch_ids: set[UUID] = set()
        async with self.session_factory() as session:
            repository = VideoScriptRepository(session)
            jobs = await repository.list_running_jobs()
            for job in jobs:
                job.status = "FAILED_RETRYABLE"
                job.last_error_code = "PROCESS_INTERRUPTED"
                job.last_error_message = "service stopped while job was running"
                job.finished_at = repository.utcnow()
                batch_ids.add(job.batch_id)
            await session.commit()
        for batch_id in batch_ids:
            await self._refresh_batch(batch_id)
        return len(jobs)

    async def shutdown(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
