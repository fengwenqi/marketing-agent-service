from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import engine
from app.hermes.contracts import MarketingContextResult
from app.hermes.fake_adapter import FakeHermesAdapter
from app.main import create_app


def blocked_result() -> MarketingContextResult:
    return MarketingContextResult.model_validate(
        {
            "schema_version": "1.0",
            "status": "BLOCKED",
            "generation_mode": None,
            "warnings_accepted": False,
            "original_request": "推广小米17 Pro",
            "goal": {"product": "小米17 Pro"},
            "product_profile": {"product_key": "xiaomi-17-pro"},
            "enterprise_profile": {},
            "marketing_evidence": {},
            "missing_required": ["target_audience"],
            "missing_optional": [],
            "conflicts": [],
            "field_sources": {},
            "evidence": [],
            "next_action": "询问目标人群",
            "next_question": "目标人群是谁？",
            "persistence": {"saved": False, "note": "由 FastAPI 持久化"},
        }
    )


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> None:
    yield
    await engine.dispose()


async def client_for(hermes: FakeHermesAdapter) -> AsyncClient:
    app = create_app(hermes=hermes)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.integration
async def test_campaign_and_history_survive_application_restart() -> None:
    marker = uuid4().hex
    first_client = await client_for(FakeHermesAdapter([blocked_result()]))
    async with first_client:
        campaign_response = await first_client.post(
            "/api/v1/campaigns",
            json={"title": f"小米活动-{marker}"},
            headers={"Idempotency-Key": f"create-{marker}"},
        )
        campaign = campaign_response.json()
        message_response = await first_client.post(
            f"/api/v1/campaigns/{campaign['id']}/messages",
            json={"content": "一个月获得500条线索"},
            headers={"Idempotency-Key": f"message-{marker}"},
        )

    restarted_client = await client_for(FakeHermesAdapter([]))
    async with restarted_client:
        detail = await restarted_client.get(f"/api/v1/campaigns/{campaign['id']}")
        history = await restarted_client.get(
            f"/api/v1/campaigns/{campaign['id']}/messages"
        )

    assert campaign_response.status_code == 201
    assert message_response.status_code == 200
    assert detail.json()["state"] == "CONTEXT_BLOCKED"
    assert detail.json()["context"]["goal"]["product"] == "小米17 Pro"
    assert [item["role"] for item in history.json()] == ["user", "assistant"]
    assert [item["sequence"] for item in history.json()] == [1, 2]


@pytest.mark.integration
async def test_duplicate_message_after_restart_does_not_call_hermes_again() -> None:
    marker = uuid4().hex
    first_hermes = FakeHermesAdapter([blocked_result()])
    first_client = await client_for(first_hermes)
    async with first_client:
        campaign = (
            await first_client.post(
                "/api/v1/campaigns",
                json={"title": f"幂等活动-{marker}"},
                headers={"Idempotency-Key": f"create-idem-{marker}"},
            )
        ).json()
        first = await first_client.post(
            f"/api/v1/campaigns/{campaign['id']}/messages",
            json={"content": "同一请求"},
            headers={"Idempotency-Key": f"same-{marker}"},
        )

    restarted_hermes = FakeHermesAdapter([])
    restarted_client = await client_for(restarted_hermes)
    async with restarted_client:
        duplicate = await restarted_client.post(
            f"/api/v1/campaigns/{campaign['id']}/messages",
            json={"content": "同一请求"},
            headers={"Idempotency-Key": f"same-{marker}"},
        )

    assert duplicate.json() == first.json()
    assert restarted_hermes.call_count == 0
