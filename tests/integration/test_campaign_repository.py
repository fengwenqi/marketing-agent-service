from uuid import uuid4

import pytest

from app.campaigns.repository import CampaignRepository
from app.database import SessionFactory, engine
from app.models.business import Campaign


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> None:
    yield
    # pytest 为每个异步测试创建事件循环，连接不能泄漏到下一个循环。
    await engine.dispose()


@pytest.mark.integration
async def test_repository_persists_campaign_messages_and_idempotency() -> None:
    marker = uuid4().hex
    campaign_id = uuid4()

    async with SessionFactory() as session:
        repository = CampaignRepository(session)
        campaign = Campaign(id=campaign_id, title=f"持久化活动-{marker}", state="DRAFT")
        repository.add_campaign(campaign)
        await repository.append_message(
            campaign_id=campaign_id,
            role="user",
            content="第一条",
            payload=None,
            idempotency_key=f"user-{marker}",
        )
        await repository.append_message(
            campaign_id=campaign_id,
            role="assistant",
            content="第二条",
            payload={"status": "BLOCKED"},
            idempotency_key=f"assistant-{marker}",
        )
        await repository.save_idempotency(
            scope=f"message:{campaign_id}",
            key=marker,
            response={"campaign_id": str(campaign_id)},
        )
        await session.commit()

    async with SessionFactory() as session:
        repository = CampaignRepository(session)
        loaded = await repository.get_campaign(campaign_id)
        messages = await repository.list_messages(campaign_id)
        cached = await repository.get_idempotency(f"message:{campaign_id}", marker)

    assert loaded is not None
    assert loaded.title == f"持久化活动-{marker}"
    assert [message.sequence for message in messages] == [1, 2]
    assert [message.content for message in messages] == ["第一条", "第二条"]
    assert cached == {"campaign_id": str(campaign_id)}


@pytest.mark.integration
async def test_lock_campaign_returns_current_persistent_state() -> None:
    campaign_id = uuid4()

    async with SessionFactory() as session:
        repository = CampaignRepository(session)
        repository.add_campaign(Campaign(id=campaign_id, title="锁测试", state="CONTEXT_BLOCKED"))
        await session.commit()

    async with SessionFactory() as session:
        locked = await CampaignRepository(session).lock_campaign(campaign_id)

    assert locked is not None
    assert locked.state == "CONTEXT_BLOCKED"
