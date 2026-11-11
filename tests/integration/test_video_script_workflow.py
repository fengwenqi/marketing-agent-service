from copy import deepcopy
from uuid import uuid4

import pytest

from app.campaigns.domain import StageCode
from app.database import SessionFactory, engine
from app.hermes.contracts import VideoScriptResult
from app.hermes.fake_adapter import FakeHermesAdapter
from app.video_scripts.executor import VideoScriptBatchExecutor
from app.video_scripts.repository import VideoScriptRepository
from app.video_scripts.service import (
    ExpectedScriptVersion,
    ScriptBatchConfirmationConflictError,
    StaleVideoScriptVersionError,
    VideoScriptBatchService,
    VideoScriptWorkflowService,
)
from tests.integration.test_video_script_batch_service import (
    ConcurrencyHermes,
    create_confirmed_media_plan,
)
from tests.unit.test_video_script_contract import valid_video_script_payload


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> None:
    yield
    await engine.dispose()


def revised_result(slot_id: str, creative_type: str, title: str) -> VideoScriptResult:
    payload = deepcopy(valid_video_script_payload())
    payload["creative_slot_id"] = slot_id
    payload["creative_type"] = creative_type
    payload["script_title"] = title
    return VideoScriptResult.model_validate(payload)


async def create_completed_batch(marker: str):
    campaign, _ = await create_confirmed_media_plan(marker)
    async with SessionFactory() as session:
        snapshot = await VideoScriptBatchService(VideoScriptRepository(session)).create_or_get(
            campaign.id, StageCode.A, f"complete-{marker}"
        )
    completed = await VideoScriptBatchExecutor(SessionFactory, ConcurrencyHermes(), 3).run_batch(
        snapshot.batch.id
    )
    return campaign, completed


@pytest.mark.integration
async def test_revision_keeps_history_and_resumes_script_session() -> None:
    marker = uuid4().hex
    campaign, completed = await create_completed_batch(marker)
    script = completed.scripts[0]
    original_session_id = script.hermes_stored_session_id
    creative_type = "oral_video" if script.creative_slot_id.startswith("oral") else "short_video"
    hermes = FakeHermesAdapter(
        [],
        video_script_results=[
            revised_result(script.creative_slot_id, creative_type, "优化后的脚本")
        ],
    )

    async with SessionFactory() as session:
        revised = await VideoScriptWorkflowService(
            VideoScriptRepository(session), hermes
        ).revise_script(
            campaign.id,
            script.id,
            expected_version=1,
            instruction="强化前三秒钩子",
            actor_id="editor-1",
            idempotency_key=f"revise-{marker}",
        )

    assert revised.current_version.version == 2
    assert revised.current_version.content["script_title"] == "优化后的脚本"
    assert [version.status for version in revised.versions] == [
        "SUPERSEDED",
        "PENDING_CONFIRMATION",
    ]
    assert revised.versions[1].parent_version_id == revised.versions[0].id
    assert hermes.video_script_requests[0].stored_session_id == original_session_id
    assert hermes.video_script_requests[0].current_script["script_title"]


@pytest.mark.integration
async def test_failed_revision_preserves_current_version_and_stale_version_rejected() -> None:
    marker = uuid4().hex
    campaign, completed = await create_completed_batch(marker)
    script = completed.scripts[0]
    async with SessionFactory() as session:
        service = VideoScriptWorkflowService(
            VideoScriptRepository(session),
            FakeHermesAdapter([], video_script_results=[RuntimeError("gateway down")]),
        )
        with pytest.raises(Exception, match="temporarily unavailable"):
            await service.revise_script(
                campaign.id,
                script.id,
                expected_version=1,
                instruction="修改表达",
                actor_id="editor-1",
                idempotency_key=f"fail-{marker}",
            )

    async with SessionFactory() as session:
        current = await VideoScriptWorkflowService(
            VideoScriptRepository(session), FakeHermesAdapter([])
        ).get_script(campaign.id, script.id)
        with pytest.raises(StaleVideoScriptVersionError):
            await VideoScriptWorkflowService(
                VideoScriptRepository(session), FakeHermesAdapter([])
            ).revise_script(
                campaign.id,
                script.id,
                expected_version=0,
                instruction="过期页面修改",
                actor_id="editor-1",
                idempotency_key=f"stale-{marker}",
            )

    assert current.current_version.version == 1
    assert len(current.versions) == 1


@pytest.mark.integration
async def test_failed_revision_can_retry_with_same_idempotency_key() -> None:
    marker = uuid4().hex
    campaign, completed = await create_completed_batch(marker)
    script = completed.scripts[0]
    creative_type = "oral_video" if script.creative_slot_id.startswith("oral") else "short_video"
    key = f"retry-{marker}"
    arguments = {
        "expected_version": 1,
        "instruction": "强化产品卖点",
        "actor_id": "editor-1",
        "idempotency_key": key,
    }

    async with SessionFactory() as session:
        failing = VideoScriptWorkflowService(
            VideoScriptRepository(session),
            FakeHermesAdapter([], video_script_results=[RuntimeError("gateway down")]),
        )
        with pytest.raises(Exception, match="temporarily unavailable"):
            await failing.revise_script(campaign.id, script.id, **arguments)

    async with SessionFactory() as session:
        succeeding = VideoScriptWorkflowService(
            VideoScriptRepository(session),
            FakeHermesAdapter(
                [],
                video_script_results=[
                    revised_result(
                        script.creative_slot_id,
                        creative_type,
                        "重试成功的脚本",
                    )
                ],
            ),
        )
        revised = await succeeding.revise_script(campaign.id, script.id, **arguments)

    assert revised.current_version.version == 2
    assert revised.current_version.content["script_title"] == "重试成功的脚本"


@pytest.mark.integration
async def test_gate_2_atomically_confirms_all_current_script_versions() -> None:
    marker = uuid4().hex
    campaign, completed = await create_completed_batch(marker)
    manifest = [
        ExpectedScriptVersion(script_id=script.id, version_id=script.current_version_id)
        for script in completed.scripts
    ]
    stale_manifest = list(manifest)
    stale_manifest[0] = ExpectedScriptVersion(
        script_id=stale_manifest[0].script_id,
        version_id=uuid4(),
    )

    async with SessionFactory() as session:
        service = VideoScriptWorkflowService(VideoScriptRepository(session), FakeHermesAdapter([]))
        with pytest.raises(ScriptBatchConfirmationConflictError):
            await service.confirm_batch(
                campaign.id,
                StageCode.A,
                expected_script_versions=stale_manifest,
                actor_id="approver-1",
                idempotency_key=f"stale-confirm-{marker}",
            )
        unchanged = await VideoScriptBatchService(VideoScriptRepository(session)).get_batch(
            campaign.id, StageCode.A
        )
        assert unchanged.batch.status == "COMPLETED"
        assert all(script.status == "PENDING_CONFIRMATION" for script in unchanged.scripts)
        confirmed = await service.confirm_batch(
            campaign.id,
            StageCode.A,
            expected_script_versions=manifest,
            actor_id="approver-1",
            idempotency_key=f"confirm-{marker}",
        )

    assert confirmed.batch.status == "CONFIRMED"
    assert confirmed.workflow.state == "VIDEO_SCRIPTS_CONFIRMED"
    assert len(confirmed.scripts) == 15
    assert all(script.status == "CONFIRMED" for script in confirmed.scripts)
    assert all(
        script.confirmed_version_id == script.current_version_id for script in confirmed.scripts
    )
    assert confirmed.confirmation_set is not None
    assert len(confirmed.confirmation_set.manifest) == 15
    assert len(confirmed.gate_decisions) == 1
    assert confirmed.gate_decisions[0].resource_version_id == confirmed.confirmation_set.id


@pytest.mark.integration
async def test_gate_2_rejects_missing_or_duplicate_manifest_without_partial_confirmation() -> None:
    marker = uuid4().hex
    campaign, completed = await create_completed_batch(marker)
    manifest = [
        ExpectedScriptVersion(script_id=script.id, version_id=script.current_version_id)
        for script in completed.scripts
    ]

    async with SessionFactory() as session:
        service = VideoScriptWorkflowService(VideoScriptRepository(session), FakeHermesAdapter([]))
        for invalid in (manifest[:-1], [manifest[0], *manifest[:-1]]):
            with pytest.raises(ScriptBatchConfirmationConflictError):
                await service.confirm_batch(
                    campaign.id,
                    StageCode.A,
                    expected_script_versions=invalid,
                    actor_id="approver-1",
                    idempotency_key=f"invalid-{len(invalid)}-{uuid4().hex}",
                )
        current = await VideoScriptBatchService(VideoScriptRepository(session)).get_batch(
            campaign.id, StageCode.A
        )

    assert current.batch.status == "COMPLETED"
    assert all(script.confirmed_version_id is None for script in current.scripts)
