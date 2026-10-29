from fastapi.testclient import TestClient

from app.hermes.contracts import MarketingContextResult
from app.hermes.fake_adapter import FakeHermesAdapter
from app.main import create_app


def blocked_result() -> MarketingContextResult:
    return MarketingContextResult.model_validate(
        {
            "status": "BLOCKED",
            "generation_mode": None,
            "goal": {"product": "小米17 Pro"},
            "product": {"product_key": "xiaomi-17-pro"},
            "missing_required": ["target_audience"],
            "missing_optional": [],
            "conflicts": [],
            "evidence": [],
            "warnings_accepted": False,
            "next_action": "询问目标人群",
        }
    )


def test_message_runs_context_intake_and_is_idempotent() -> None:
    hermes = FakeHermesAdapter([blocked_result()])
    client = TestClient(create_app(hermes=hermes))
    campaign = client.post(
        "/api/v1/campaigns",
        json={"title": "小米17 Pro 获客"},
        headers={"Idempotency-Key": "create-1"},
    ).json()

    first = client.post(
        f"/api/v1/campaigns/{campaign['id']}/messages",
        json={"content": "推广小米17 Pro，一个月获得500条线索"},
        headers={"Idempotency-Key": "message-1"},
    )
    second = client.post(
        f"/api/v1/campaigns/{campaign['id']}/messages",
        json={"content": "推广小米17 Pro，一个月获得500条线索"},
        headers={"Idempotency-Key": "message-1"},
    )

    assert first.status_code == 200
    assert first.json()["campaign_state"] == "CONTEXT_BLOCKED"
    assert second.json() == first.json()
    assert hermes.call_count == 1


def test_message_in_ready_state_returns_conflict() -> None:
    ready = blocked_result().model_copy(
        update={"status": "READY", "missing_required": [], "next_action": "进入营销计划"}
    )
    client = TestClient(create_app(hermes=FakeHermesAdapter([ready])))
    campaign = client.post(
        "/api/v1/campaigns",
        json={"title": "活动"},
        headers={"Idempotency-Key": "create-ready"},
    ).json()
    url = f"/api/v1/campaigns/{campaign['id']}/messages"
    client.post(url, json={"content": "目标完整"}, headers={"Idempotency-Key": "m-ready"})

    response = client.post(
        url,
        json={"content": "不应继续采集"},
        headers={"Idempotency-Key": "m-conflict"},
    )

    assert response.status_code == 409

