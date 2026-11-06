from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import engine
from app.hermes.fake_adapter import FakeHermesAdapter
from app.hermes.ports import HermesOutputValidationError
from app.main import create_app
from tests.integration.test_media_plan_service import (
    create_confirmed_campaign,
    media_plan_result,
)


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> None:
    yield
    await engine.dispose()


def client_for(hermes: FakeHermesAdapter) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=create_app(hermes=hermes)),
        base_url="http://test",
    )


@pytest.mark.integration
async def test_A_plan_api_draft_revision_confirmation_and_restart() -> None:
    marker = uuid4().hex
    campaign, _, _ = await create_confirmed_campaign(marker)
    hermes = FakeHermesAdapter(
        [],
        media_plan_results=[
            media_plan_result(summary="V1 草案", complete=False),
            media_plan_result(summary="V2 补参", complete=True),
            media_plan_result(summary="V3 优化", complete=True),
        ],
    )
    async with client_for(hermes) as client:
        generated = await client.post(
            f"/api/v1/campaigns/{campaign.id}/media-plans/A/generate",
            headers={"Idempotency-Key": f"generate-{marker}"},
            json={"confirmation_inputs": {}},
        )
        empty_revision = await client.post(
            f"/api/v1/campaigns/{campaign.id}/media-plans/A/revise",
            headers={"Idempotency-Key": f"empty-revision-{marker}"},
            json={
                "expected_version": 1,
                "actor_id": "planner",
                "confirmation_inputs": {},
            },
        )
        incomplete_confirmation = await client.post(
            f"/api/v1/campaigns/{campaign.id}/media-plans/A/confirm",
            headers={"Idempotency-Key": f"confirm-incomplete-{marker}"},
            json={"expected_version": 1, "actor_id": "approver"},
        )
        supplemented = await client.post(
            f"/api/v1/campaigns/{campaign.id}/media-plans/A/revise",
            headers={"Idempotency-Key": f"supplement-{marker}"},
            json={
                "expected_version": 1,
                "actor_id": "planner",
                "confirmation_inputs": {
                    "campaign_total_budget": "10000.00",
                    "stage_start_date": "2026-11-10",
                    "stage_end_date": "2026-11-20",
                    "target_roi": "1.50",
                },
            },
        )
        revised = await client.post(
            f"/api/v1/campaigns/{campaign.id}/media-plans/A/revise",
            headers={"Idempotency-Key": f"revise-{marker}"},
            json={
                "expected_version": 2,
                "actor_id": "planner",
                "instruction": "强化核心人群测试",
            },
        )
        stale_confirmation = await client.post(
            f"/api/v1/campaigns/{campaign.id}/media-plans/A/confirm",
            headers={"Idempotency-Key": f"confirm-stale-{marker}"},
            json={"expected_version": 2, "actor_id": "approver"},
        )
        confirmed = await client.post(
            f"/api/v1/campaigns/{campaign.id}/media-plans/A/confirm",
            headers={"Idempotency-Key": f"confirm-{marker}"},
            json={"expected_version": 3, "actor_id": "approver"},
        )

    async with client_for(FakeHermesAdapter([])) as restarted:
        loaded = await restarted.get(
            f"/api/v1/campaigns/{campaign.id}/media-plans/A"
        )

    assert generated.status_code == 200
    assert empty_revision.status_code == 422
    assert generated.json()["current_version"]["missing_confirmation_fields"]
    assert incomplete_confirmation.status_code == 422
    assert incomplete_confirmation.json()["detail"]["code"] == "PLAN_NOT_CONFIRMABLE"
    assert supplemented.json()["current_version"]["version"] == 2
    assert supplemented.json()["current_version"]["validation_result"][
        "is_confirmable"
    ] is True
    assert revised.json()["current_version"]["version"] == 3
    assert stale_confirmation.status_code == 409
    assert confirmed.status_code == 200
    assert confirmed.json()["workflow_state"] == "MEDIA_PLAN_CONFIRMED"
    assert loaded.json()["confirmed_version_id"] == loaded.json()["current_version_id"]
    assert [item["version"] for item in loaded.json()["versions"]] == [1, 2, 3]


@pytest.mark.integration
@pytest.mark.parametrize("stage_code", ["B", "C"])
async def test_B_C_media_plan_endpoints_are_not_open(stage_code: str) -> None:
    marker = uuid4().hex
    campaign, _, _ = await create_confirmed_campaign(marker)
    async with client_for(FakeHermesAdapter([])) as client:
        response = await client.post(
            f"/api/v1/campaigns/{campaign.id}/media-plans/{stage_code}/generate",
            headers={"Idempotency-Key": f"closed-{stage_code}-{marker}"},
            json={"confirmation_inputs": {}},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "STAGE_NOT_OPEN"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (
            HermesOutputValidationError("invalid output"),
            502,
            "INVALID_MEDIA_PLAN_OUTPUT",
        ),
        (RuntimeError("gateway down"), 503, "MEDIA_PLAN_SERVICE_UNAVAILABLE"),
    ],
)
async def test_media_plan_api_maps_Hermes_failures(
    error: Exception, expected_status: int, expected_code: str
) -> None:
    marker = uuid4().hex
    campaign, _, _ = await create_confirmed_campaign(marker)
    async with client_for(
        FakeHermesAdapter([], media_plan_results=[error])
    ) as client:
        response = await client.post(
            f"/api/v1/campaigns/{campaign.id}/media-plans/A/generate",
            headers={"Idempotency-Key": f"error-{expected_status}-{marker}"},
            json={"confirmation_inputs": {}},
        )

    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code
