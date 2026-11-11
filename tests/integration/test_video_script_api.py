import asyncio
from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.campaigns.domain import StageCode
from app.database import SessionFactory, engine
from app.hermes.fake_adapter import FakeHermesAdapter
from app.main import create_app
from app.video_scripts.executor import VideoScriptBatchExecutor
from app.video_scripts.repository import VideoScriptRepository
from app.video_scripts.service import VideoScriptBatchService
from tests.integration.test_video_script_batch_service import (
    ConcurrencyHermes,
    create_confirmed_media_plan,
)
from tests.unit.test_video_script_contract import valid_video_script_payload


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> None:
    yield
    await engine.dispose()


class RecordingExecutor:
    def __init__(self) -> None:
        self.batch_ids: list[UUID] = []

    def schedule(self, batch_id: UUID) -> None:
        self.batch_ids.append(batch_id)


@pytest.mark.integration
async def test_create_batch_api_returns_202_and_schedules_background_work() -> None:
    marker = uuid4().hex
    campaign, _ = await create_confirmed_media_plan(marker)
    app = create_app(hermes=FakeHermesAdapter([]))
    recorder = RecordingExecutor()
    app.state.video_script_executor = recorder

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/campaigns/{campaign.id}/video-script-batches/A",
            headers={"Idempotency-Key": f"create-{marker}"},
        )

    assert response.status_code == 202
    assert response.json()["counts"]["pending"] == 15
    assert recorder.batch_ids == [UUID(response.json()["batch_id"])]


@pytest.mark.integration
async def test_executor_deduplicates_same_batch_schedule() -> None:
    executor = VideoScriptBatchExecutor(SessionFactory, FakeHermesAdapter([]), 3)
    batch_id = uuid4()
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[UUID] = []

    async def run_once(requested_batch_id: UUID):
        calls.append(requested_batch_id)
        started.set()
        await release.wait()

    executor.run_batch = run_once  # type: ignore[method-assign]
    executor.schedule(batch_id)
    await started.wait()
    executor.schedule(batch_id)
    release.set()
    await executor.shutdown()

    assert calls == [batch_id]


@pytest.mark.integration
async def test_script_api_revision_and_batch_gate_2_confirmation() -> None:
    marker = uuid4().hex
    campaign, _ = await create_confirmed_media_plan(marker)
    async with SessionFactory() as session:
        created = await VideoScriptBatchService(VideoScriptRepository(session)).create_or_get(
            campaign.id, StageCode.A, f"complete-{marker}"
        )
    completed = await VideoScriptBatchExecutor(SessionFactory, ConcurrencyHermes(), 3).run_batch(
        created.batch.id
    )
    first = completed.scripts[0]
    revised_payload = deepcopy(valid_video_script_payload())
    revised_payload["creative_slot_id"] = first.creative_slot_id
    revised_payload["creative_type"] = (
        "oral_video" if first.creative_slot_id.startswith("oral") else "short_video"
    )
    revised_payload["script_title"] = "API 优化后的脚本"
    from app.hermes.contracts import VideoScriptResult

    app = create_app(
        hermes=FakeHermesAdapter(
            [],
            video_script_results=[VideoScriptResult.model_validate(revised_payload)],
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        revised = await client.post(
            f"/api/v1/campaigns/{campaign.id}/video-scripts/A/{first.id}/revise",
            headers={"Idempotency-Key": f"revise-{marker}"},
            json={
                "expected_version": 1,
                "instruction": "强化开头钩子",
                "actor_id": "editor-1",
            },
        )
        listed = await client.get(f"/api/v1/campaigns/{campaign.id}/video-scripts/A")
        manifest = [
            {
                "script_id": item["id"],
                "version_id": item["current_version_id"],
            }
            for item in listed.json()["scripts"]
        ]
        confirmed = await client.post(
            f"/api/v1/campaigns/{campaign.id}/video-script-batches/A/confirm",
            headers={"Idempotency-Key": f"confirm-{marker}"},
            json={
                "actor_id": "approver-1",
                "expected_script_versions": manifest,
            },
        )

    assert revised.status_code == 200
    assert revised.json()["current_version"]["version"] == 2
    assert revised.json()["current_version"]["content"]["script_title"] == "API 优化后的脚本"
    assert listed.status_code == 200
    assert len(listed.json()["scripts"]) == 15
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CONFIRMED"
    assert confirmed.json()["workflow_state"] == "VIDEO_SCRIPTS_CONFIRMED"
    assert confirmed.json()["confirmation_set_id"] is not None
