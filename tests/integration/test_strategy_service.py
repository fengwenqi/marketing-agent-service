from copy import deepcopy
from uuid import uuid4

import pytest

from app.campaigns.domain import CampaignState
from app.database import SessionFactory, engine
from app.hermes.contracts import MarketingStrategyResult
from app.hermes.fake_adapter import FakeHermesAdapter
from app.models.business import Campaign
from app.strategies.repository import StrategyRepository
from app.strategies.service import (
    ExternalStrategyServiceError,
    StaleStrategyVersionError,
    StrategyService,
)
from tests.unit.test_strategy_contract import valid_strategy_payload


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> None:
    yield
    await engine.dispose()


def strategy_result(summary: str) -> MarketingStrategyResult:
    payload = deepcopy(valid_strategy_payload())
    payload["framework_summary"] = summary
    return MarketingStrategyResult.model_validate(payload)


async def create_ready_campaign(marker: str) -> Campaign:
    async with SessionFactory() as session:
        campaign = Campaign(
            title=f"策略活动-{marker}",
            state=CampaignState.CONTEXT_READY.value,
            context={
                "status": "READY",
                "goal": {"product": "小米17 Pro", "target": "500条线索"},
            },
        )
        session.add(campaign)
        await session.commit()
        return campaign


@pytest.mark.integration
async def test_generate_and_revise_preserve_complete_version_chain() -> None:
    marker = uuid4().hex
    campaign = await create_ready_campaign(marker)
    hermes = FakeHermesAdapter(
        [], strategy_results=[strategy_result("初版"), strategy_result("修改版")]
    )

    async with SessionFactory() as session:
        service = StrategyService(StrategyRepository(session), hermes)
        initial = await service.generate(campaign.id, f"generate-{marker}")
        assert initial.current_version.version == 1
        revised = await service.revise(
            campaign.id,
            expected_version=1,
            instruction="加强 B 阶段的口碑内容",
            idempotency_key=f"revise-{marker}",
            actor_id="user-1",
        )

    assert revised.current_version.version == 2
    assert [version.status for version in revised.versions] == [
        "SUPERSEDED",
        "PENDING_CONFIRMATION",
    ]
    assert revised.versions[1].parent_version_id == revised.versions[0].id
    assert revised.versions[1].revision_instruction == "加强 B 阶段的口碑内容"
    assert hermes.strategy_requests[1].current_strategy["framework_summary"] == "初版"
    assert hermes.strategy_requests[1].revision_instruction == "加强 B 阶段的口碑内容"
    assert hermes.strategy_requests[1].stored_session_id is not None


@pytest.mark.integration
async def test_failed_revision_preserves_current_version_and_can_retry_same_key() -> None:
    marker = uuid4().hex
    key = f"retry-revision-{marker}"
    campaign = await create_ready_campaign(marker)

    async with SessionFactory() as session:
        service = StrategyService(
            StrategyRepository(session),
            FakeHermesAdapter([], strategy_results=[strategy_result("初版")]),
        )
        await service.generate(campaign.id, f"generate-retry-{marker}")

    async with SessionFactory() as session:
        failing = StrategyService(
            StrategyRepository(session),
            FakeHermesAdapter([], strategy_results=[RuntimeError("gateway down")]),
        )
        with pytest.raises(ExternalStrategyServiceError):
            await failing.revise(
                campaign.id,
                expected_version=1,
                instruction="修改 A 阶段",
                idempotency_key=key,
                actor_id="user-1",
            )

    async with SessionFactory() as session:
        retry_hermes = FakeHermesAdapter(
            [], strategy_results=[strategy_result("重试成功版")]
        )
        retrying = StrategyService(StrategyRepository(session), retry_hermes)
        snapshot = await retrying.revise(
            campaign.id,
            expected_version=1,
            instruction="修改 A 阶段",
            idempotency_key=key,
            actor_id="user-1",
        )

    assert snapshot.current_version.version == 2
    assert len(snapshot.versions) == 2


@pytest.mark.integration
async def test_stale_revision_is_rejected_before_calling_hermes() -> None:
    marker = uuid4().hex
    campaign = await create_ready_campaign(marker)
    initial_hermes = FakeHermesAdapter(
        [], strategy_results=[strategy_result("初版")]
    )
    async with SessionFactory() as session:
        await StrategyService(StrategyRepository(session), initial_hermes).generate(
            campaign.id, f"generate-stale-{marker}"
        )

    stale_hermes = FakeHermesAdapter([], strategy_results=[])
    async with SessionFactory() as session:
        service = StrategyService(StrategyRepository(session), stale_hermes)
        with pytest.raises(StaleStrategyVersionError):
            await service.revise(
                campaign.id,
                expected_version=0,
                instruction="过期页面提交",
                idempotency_key=f"stale-{marker}",
                actor_id="user-1",
            )

    assert stale_hermes.strategy_call_count == 0


@pytest.mark.integration
async def test_gate1_confirms_exact_current_version_and_survives_restart() -> None:
    marker = uuid4().hex
    campaign = await create_ready_campaign(marker)
    async with SessionFactory() as session:
        service = StrategyService(
            StrategyRepository(session),
            FakeHermesAdapter([], strategy_results=[strategy_result("最终版")]),
        )
        await service.generate(campaign.id, f"generate-confirm-{marker}")
        confirmed = await service.confirm(
            campaign.id,
            expected_version=1,
            actor_id="user-1",
            idempotency_key=f"confirm-{marker}",
        )

    async with SessionFactory() as session:
        restarted = StrategyService(
            StrategyRepository(session), FakeHermesAdapter([], strategy_results=[])
        )
        loaded = await restarted.get_for_campaign(campaign.id)

    assert confirmed.framework.confirmed_version_id == confirmed.current_version.id
    assert confirmed.current_version.status == "CONFIRMED"
    assert loaded.framework.status == "CONFIRMED"
    assert loaded.campaign.state == CampaignState.STRATEGY_CONFIRMED.value
    assert loaded.decisions[-1].resource_version_id == loaded.current_version.id
    assert loaded.decisions[-1].decision == "APPROVE"
