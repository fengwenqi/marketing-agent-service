from copy import deepcopy
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.campaigns.domain import CampaignState
from app.database import SessionFactory, engine
from app.hermes.contracts import MarketingStrategyResult
from app.hermes.fake_adapter import FakeHermesAdapter
from app.main import create_app
from app.models.business import Campaign
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
            title=f"接口策略活动-{marker}",
            state=CampaignState.CONTEXT_READY.value,
            context={"status": "READY", "goal": {"product": "小米17 Pro"}},
        )
        session.add(campaign)
        await session.commit()
        return campaign


@pytest.mark.integration
async def test_strategy_api_generates_revises_and_confirms_exact_version() -> None:
    marker = uuid4().hex
    campaign = await create_ready_campaign(marker)
    hermes = FakeHermesAdapter(
        [], strategy_results=[strategy_result("初版"), strategy_result("修改版")]
    )
    app = create_app(hermes=hermes)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        generated = await client.post(
            f"/api/v1/campaigns/{campaign.id}/strategy-framework/generate",
            headers={"Idempotency-Key": f"generate-{marker}"},
        )
        revised = await client.post(
            f"/api/v1/campaigns/{campaign.id}/strategy-framework/revise",
            headers={"Idempotency-Key": f"revise-{marker}"},
            json={
                "expected_version": 1,
                "instruction": "加强 B 阶段口碑内容",
                "actor_id": "tester-1",
            },
        )
        confirmed = await client.post(
            f"/api/v1/campaigns/{campaign.id}/strategy-framework/confirm",
            headers={"Idempotency-Key": f"confirm-{marker}"},
            json={"expected_version": 2, "actor_id": "tester-1"},
        )
        conflicting_retry = await client.post(
            f"/api/v1/campaigns/{campaign.id}/strategy-framework/confirm",
            headers={"Idempotency-Key": f"confirm-{marker}"},
            json={"expected_version": 2, "actor_id": "different-user"},
        )

    assert generated.status_code == 200
    assert [item["version"] for item in revised.json()["versions"]] == [1, 2]
    assert revised.json()["gate_decisions"][0]["decision"] == "REVISION_REQUESTED"
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CONFIRMED"
    assert confirmed.json()["campaign_state"] == "STRATEGY_CONFIRMED"
    assert confirmed.json()["confirmed_version_id"] == confirmed.json()["current_version_id"]
    assert confirmed.json()["gate_decisions"][-1]["decision"] == "APPROVE"
    assert conflicting_retry.status_code == 409


@pytest.mark.integration
async def test_strategy_api_rejects_stale_revision_and_survives_restart() -> None:
    marker = uuid4().hex
    campaign = await create_ready_campaign(marker)
    first_app = create_app(
        hermes=FakeHermesAdapter([], strategy_results=[strategy_result("初版")])
    )
    async with AsyncClient(
        transport=ASGITransport(app=first_app), base_url="http://test"
    ) as client:
        await client.post(
            f"/api/v1/campaigns/{campaign.id}/strategy-framework/generate",
            headers={"Idempotency-Key": f"generate-restart-{marker}"},
        )

    restarted_app = create_app(hermes=FakeHermesAdapter([], strategy_results=[]))
    async with AsyncClient(
        transport=ASGITransport(app=restarted_app), base_url="http://test"
    ) as client:
        loaded = await client.get(
            f"/api/v1/campaigns/{campaign.id}/strategy-framework"
        )
        stale = await client.post(
            f"/api/v1/campaigns/{campaign.id}/strategy-framework/revise",
            headers={"Idempotency-Key": f"stale-{marker}"},
            json={
                "expected_version": 99,
                "instruction": "基于旧页面修改",
                "actor_id": "tester-1",
            },
        )

    assert loaded.status_code == 200
    assert loaded.json()["versions"][0]["content"]["framework_summary"] == "初版"
    assert stale.status_code == 409
