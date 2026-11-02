from uuid import uuid4

import pytest

from app.campaigns.repository import CampaignRepository
from app.campaigns.service import CampaignService
from app.database import SessionFactory, engine
from app.hermes.contracts import MarketingContextResult
from app.hermes.fake_adapter import FakeHermesAdapter
from app.hermes.ports import ContextIntakeResponse


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
            "persistence": {"saved": False, "note": "由 FastAPI 负责持久化"},
        }
    )


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> None:
    yield
    await engine.dispose()


@pytest.mark.integration
async def test_create_is_durable_and_idempotent_across_service_instances() -> None:
    key = uuid4().hex
    async with SessionFactory() as session:
        first_service = CampaignService(CampaignRepository(session), FakeHermesAdapter([]))
        first = await first_service.create(f"活动-{key}", key)

    async with SessionFactory() as session:
        second_service = CampaignService(CampaignRepository(session), FakeHermesAdapter([]))
        duplicate = await second_service.create(f"被忽略-{key}", key)
        loaded = await second_service.get(first.id)

    assert duplicate.id == first.id
    assert loaded.title == f"活动-{key}"


@pytest.mark.integration
async def test_submit_message_persists_history_and_context() -> None:
    marker = uuid4().hex
    hermes = FakeHermesAdapter([blocked_result()])
    async with SessionFactory() as session:
        service = CampaignService(CampaignRepository(session), hermes)
        campaign = await service.create(f"活动-{marker}", f"create-{marker}")
        response = await service.submit_message(
            campaign.id, "推广小米17 Pro", f"message-{marker}"
        )

    async with SessionFactory() as session:
        restarted = CampaignService(CampaignRepository(session), FakeHermesAdapter([]))
        loaded = await restarted.get(campaign.id)
        history = await restarted.list_messages(campaign.id)

    assert response["campaign_state"] == "CONTEXT_BLOCKED"
    assert loaded.context is not None
    assert [item.role for item in history] == ["user", "assistant"]
    assert history[1].content == "目标人群是谁？"


@pytest.mark.integration
async def test_second_turn_reuses_persisted_hermes_session_and_context() -> None:
    marker = uuid4().hex
    first_result = blocked_result()
    second_result = blocked_result().model_copy(
        update={"next_question": "预算是多少？", "next_action": "询问预算"}
    )
    first_hermes = FakeHermesAdapter(
        [ContextIntakeResponse(first_result, stored_session_id=f"stored-{marker}")]
    )

    async with SessionFactory() as session:
        service = CampaignService(CampaignRepository(session), first_hermes)
        campaign = await service.create(f"活动-{marker}", f"create-session-{marker}")
        await service.submit_message(campaign.id, "第一轮", f"turn-1-{marker}")

    second_hermes = FakeHermesAdapter(
        [ContextIntakeResponse(second_result, stored_session_id=f"stored-{marker}")]
    )
    async with SessionFactory() as session:
        restarted = CampaignService(CampaignRepository(session), second_hermes)
        await restarted.submit_message(campaign.id, "第二轮", f"turn-2-{marker}")
        loaded = await restarted.get(campaign.id)

    request = second_hermes.requests[0]
    assert request.stored_session_id == f"stored-{marker}"
    assert request.context is not None
    assert [message["content"] for message in request.recent_messages or []] == [
        "第一轮",
        "目标人群是谁？",
        "第二轮",
    ]
    assert loaded.hermes_stored_session_id == f"stored-{marker}"
