import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest

from app.campaigns.domain import CampaignState, StageCode, StageWorkflowState
from app.database import SessionFactory, engine
from app.hermes.contracts import VideoScriptResult
from app.hermes.fake_adapter import FakeHermesAdapter
from app.hermes.ports import VideoScriptGenerationResponse
from app.models.business import (
    Campaign,
    CampaignStageWorkflow,
    MarketingStrategyFramework,
    MarketingStrategyVersion,
    StageMediaPlan,
    StageMediaPlanVersion,
    VideoScriptJob,
)
from app.video_scripts.executor import VideoScriptBatchExecutor
from app.video_scripts.repository import VideoScriptRepository
from app.video_scripts.service import StageNotOpenError, VideoScriptBatchService
from tests.unit.test_media_plan_contract import valid_media_plan_payload
from tests.unit.test_strategy_contract import valid_strategy_payload
from tests.unit.test_video_script_contract import valid_video_script_payload


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> None:
    yield
    await engine.dispose()


async def create_confirmed_media_plan(marker: str) -> tuple[Campaign, StageMediaPlan]:
    async with SessionFactory() as session:
        campaign = Campaign(
            title=f"视频脚本活动-{marker}",
            state=CampaignState.STAGE_EXECUTION.value,
            context={"status": "READY", "goal": {"product": "小米17 Pro"}},
        )
        session.add(campaign)
        await session.flush()
        framework = MarketingStrategyFramework(
            campaign_id=campaign.id,
            status="CONFIRMED",
        )
        session.add(framework)
        await session.flush()
        strategy_version = MarketingStrategyVersion(
            framework_id=framework.id,
            version=1,
            status="CONFIRMED",
            content=valid_strategy_payload(),
        )
        session.add(strategy_version)
        await session.flush()
        framework.current_version_id = strategy_version.id
        framework.confirmed_version_id = strategy_version.id
        workflow = CampaignStageWorkflow(
            campaign_id=campaign.id,
            stage_code="A",
            state=StageWorkflowState.MEDIA_PLAN_CONFIRMED.value,
            version=1,
        )
        session.add(workflow)
        await session.flush()
        plan = StageMediaPlan(
            campaign_id=campaign.id,
            stage_workflow_id=workflow.id,
            stage_code="A",
            source_strategy_framework_id=framework.id,
            source_strategy_version_id=strategy_version.id,
            status="CONFIRMED",
        )
        session.add(plan)
        await session.flush()
        plan_version = StageMediaPlanVersion(
            media_plan_id=plan.id,
            version=1,
            status="CONFIRMED",
            confirmation_inputs={
                "campaign_total_budget": "10000.00",
                "stage_start_date": "2026-11-10",
                "stage_end_date": "2026-11-20",
                "target_roi": "1.50",
            },
            missing_confirmation_fields=[],
            validation_result={"is_confirmable": True, "errors": []},
            content=valid_media_plan_payload(),
        )
        session.add(plan_version)
        await session.flush()
        plan.current_version_id = plan_version.id
        plan.confirmed_version_id = plan_version.id
        await session.commit()
        return campaign, plan


def script_result(slot_id: str, creative_type: str) -> VideoScriptResult:
    payload = deepcopy(valid_video_script_payload())
    payload["creative_slot_id"] = slot_id
    payload["creative_type"] = creative_type
    return VideoScriptResult.model_validate(payload)


@pytest.mark.integration
async def test_batch_creation_is_idempotent_and_creates_exactly_fifteen_jobs() -> None:
    marker = uuid4().hex
    campaign, plan = await create_confirmed_media_plan(marker)
    async with SessionFactory() as session:
        service = VideoScriptBatchService(VideoScriptRepository(session))
        created = await service.create_or_get(campaign.id, StageCode.A, f"create-{marker}")
    async with SessionFactory() as session:
        duplicate = await VideoScriptBatchService(VideoScriptRepository(session)).create_or_get(
            campaign.id, StageCode.A, f"other-key-{marker}"
        )

    assert created.batch.id == duplicate.batch.id
    assert created.batch.source_media_plan_version_id == plan.confirmed_version_id
    assert created.counts == {
        "pending": 15,
        "running": 0,
        "succeeded": 0,
        "failed": 0,
    }
    assert len(created.jobs) == 15
    assert len({job.creative_slot_id for job in created.jobs}) == 15


@pytest.mark.integration
async def test_concurrent_batch_creation_returns_one_batch() -> None:
    marker = uuid4().hex
    campaign, _ = await create_confirmed_media_plan(marker)

    async def create(key: str):
        async with SessionFactory() as session:
            return await VideoScriptBatchService(VideoScriptRepository(session)).create_or_get(
                campaign.id, StageCode.A, key
            )

    first, second = await asyncio.gather(
        create(f"concurrent-a-{marker}"), create(f"concurrent-b-{marker}")
    )

    assert first.batch.id == second.batch.id
    assert len(first.jobs) == 15
    assert len(second.jobs) == 15


@pytest.mark.integration
async def test_batch_creation_rejects_B_and_C() -> None:
    marker = uuid4().hex
    campaign, _ = await create_confirmed_media_plan(marker)

    async with SessionFactory() as session:
        service = VideoScriptBatchService(VideoScriptRepository(session))
        with pytest.raises(StageNotOpenError):
            await service.create_or_get(campaign.id, StageCode.B, f"b-{marker}")
        with pytest.raises(StageNotOpenError):
            await service.create_or_get(campaign.id, StageCode.C, f"c-{marker}")


class ConcurrencyHermes(FakeHermesAdapter):
    def __init__(
        self,
        fail_slots: set[str] | None = None,
        wrong_output_slots: set[str] | None = None,
    ):
        super().__init__([])
        self.active = 0
        self.max_active = 0
        self.fail_slots = fail_slots or set()
        self.wrong_output_slots = wrong_output_slots or set()

    async def run_video_script_generation(self, request):
        self.video_script_call_count += 1
        self.video_script_requests.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        # 数据库领取任务也需要时间；保持调用足够久，才能观测执行器的并发上限。
        await asyncio.sleep(0.15)
        try:
            if request.creative_slot["id"] in self.fail_slots:
                raise RuntimeError("gateway down")
            result_slot = (
                "wrong-slot"
                if request.creative_slot["id"] in self.wrong_output_slots
                else request.creative_slot["id"]
            )
            return VideoScriptGenerationResponse(
                result=script_result(result_slot, request.creative_slot["format"]),
                stored_session_id=f"stored-{request.creative_slot['id']}",
            )
        finally:
            self.active -= 1


@pytest.mark.integration
async def test_executor_limits_concurrency_and_completes_all_scripts() -> None:
    marker = uuid4().hex
    campaign, _ = await create_confirmed_media_plan(marker)
    async with SessionFactory() as session:
        snapshot = await VideoScriptBatchService(VideoScriptRepository(session)).create_or_get(
            campaign.id, StageCode.A, f"execute-{marker}"
        )
    hermes = ConcurrencyHermes()
    executor = VideoScriptBatchExecutor(
        session_factory=SessionFactory,
        hermes=hermes,
        max_concurrency=3,
    )

    completed = await executor.run_batch(snapshot.batch.id)

    assert hermes.max_active == 3
    assert completed.counts["succeeded"] == 15
    assert completed.counts["failed"] == 0
    assert len(completed.scripts) == 15
    assert completed.workflow.state == "VIDEO_SCRIPTS_PENDING_CONFIRMATION"


@pytest.mark.integration
async def test_partial_failure_retries_only_failed_job() -> None:
    marker = uuid4().hex
    campaign, _ = await create_confirmed_media_plan(marker)
    async with SessionFactory() as session:
        snapshot = await VideoScriptBatchService(VideoScriptRepository(session)).create_or_get(
            campaign.id, StageCode.A, f"partial-{marker}"
        )
    failed_slot = snapshot.jobs[4].creative_slot_id
    first = VideoScriptBatchExecutor(SessionFactory, ConcurrencyHermes({failed_slot}), 3)

    partial = await first.run_batch(snapshot.batch.id)

    assert partial.counts["succeeded"] == 14
    assert partial.counts["failed"] == 1
    assert partial.workflow.state == "VIDEO_SCRIPTS_PARTIAL_FAILURE"
    failed_slot = next(
        job.creative_slot_id for job in partial.jobs if job.status == "FAILED_RETRYABLE"
    )

    async with SessionFactory() as session:
        retried = await VideoScriptBatchService(VideoScriptRepository(session)).retry_failed(
            campaign.id, StageCode.A, f"retry-{marker}"
        )
    retry_hermes = ConcurrencyHermes()
    completed = await VideoScriptBatchExecutor(SessionFactory, retry_hermes, 3).run_batch(
        retried.batch.id
    )

    assert retry_hermes.video_script_call_count == 1
    assert completed.counts["succeeded"] == 15
    assert len(completed.scripts) == 15


@pytest.mark.integration
async def test_invalid_slot_output_fails_only_claimed_job() -> None:
    marker = uuid4().hex
    campaign, _ = await create_confirmed_media_plan(marker)
    async with SessionFactory() as session:
        snapshot = await VideoScriptBatchService(VideoScriptRepository(session)).create_or_get(
            campaign.id, StageCode.A, f"invalid-output-{marker}"
        )
    wrong_slot = snapshot.jobs[3].creative_slot_id

    partial = await VideoScriptBatchExecutor(
        SessionFactory,
        ConcurrencyHermes(wrong_output_slots={wrong_slot}),
        3,
    ).run_batch(snapshot.batch.id)

    assert partial.counts == {
        "pending": 0,
        "running": 0,
        "succeeded": 14,
        "failed": 1,
    }
    assert len(partial.scripts) == 14
    assert wrong_slot not in {script.creative_slot_id for script in partial.scripts}


@pytest.mark.integration
async def test_recovery_marks_interrupted_jobs_retryable() -> None:
    marker = uuid4().hex
    campaign, _ = await create_confirmed_media_plan(marker)
    async with SessionFactory() as session:
        snapshot = await VideoScriptBatchService(VideoScriptRepository(session)).create_or_get(
            campaign.id, StageCode.A, f"recover-{marker}"
        )
        job = await session.get(VideoScriptJob, snapshot.jobs[0].id)
        job.status = "RUNNING"
        await session.commit()

    recovered = await VideoScriptBatchExecutor(
        SessionFactory, FakeHermesAdapter([]), 3
    ).recover_interrupted()

    async with SessionFactory() as session:
        current = await VideoScriptBatchService(VideoScriptRepository(session)).get_batch(
            campaign.id, StageCode.A
        )
    assert recovered == 1
    assert current.counts["failed"] == 1
    assert current.workflow.state == "VIDEO_SCRIPTS_PARTIAL_FAILURE"
