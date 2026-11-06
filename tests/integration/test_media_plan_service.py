from copy import deepcopy
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.campaigns.domain import CampaignState, StageCode, StageWorkflowState
from app.database import SessionFactory, engine
from app.hermes.contracts import StageMediaPlanResult
from app.hermes.fake_adapter import FakeHermesAdapter
from app.media_plans.repository import MediaPlanRepository
from app.media_plans.service import (
    ExternalMediaPlanServiceError,
    MediaPlanIdempotencyConflictError,
    MediaPlanNotConfirmableError,
    MediaPlanService,
    StageNotOpenError,
    StaleMediaPlanVersionError,
    StaleStrategySourceError,
)
from app.media_plans.validation import ConfirmationInputs
from app.models.business import (
    Campaign,
    CampaignStageWorkflow,
    MarketingStrategyFramework,
    MarketingStrategyVersion,
)
from tests.unit.test_media_plan_contract import valid_media_plan_payload
from tests.unit.test_strategy_contract import valid_strategy_payload


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> None:
    yield
    await engine.dispose()


def complete_inputs() -> ConfirmationInputs:
    return ConfirmationInputs(
        campaign_total_budget=Decimal("10000.00"),
        stage_start_date=date(2026, 11, 10),
        stage_end_date=date(2026, 11, 20),
        target_roi=Decimal("1.50"),
    )


def media_plan_result(*, summary: str, complete: bool) -> StageMediaPlanResult:
    payload = deepcopy(valid_media_plan_payload())
    payload["plan_summary"] = summary
    if complete:
        payload["delivery_objective"]["target_roi"] = "1.50"
        payload["budget_plan"]["stage_budget"] = "5000.00"
        payload["budget_plan"]["units"][0]["amount"] = "5000.00"
        payload["schedule"][0]["start_date"] = "2026-11-10"
        payload["schedule"][0]["end_date"] = "2026-11-20"
    return StageMediaPlanResult.model_validate(payload)


async def create_confirmed_campaign(marker: str) -> tuple[Campaign, UUID, UUID]:
    async with SessionFactory() as session:
        campaign = Campaign(
            title=f"投放计划活动-{marker}",
            state=CampaignState.STRATEGY_CONFIRMED.value,
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
        version = MarketingStrategyVersion(
            framework_id=framework.id,
            version=1,
            status="CONFIRMED",
            content=valid_strategy_payload(),
        )
        session.add(version)
        await session.flush()
        framework.current_version_id = version.id
        framework.confirmed_version_id = version.id
        for stage in StageCode:
            session.add(
                CampaignStageWorkflow(
                    campaign_id=campaign.id,
                    stage_code=stage.value,
                    state=StageWorkflowState.NOT_STARTED.value,
                    version=1,
                )
            )
        await session.commit()
        return campaign, framework.id, version.id


@pytest.mark.integration
async def test_generate_A_draft_binds_confirmed_strategy_and_blocks_B_C() -> None:
    marker = uuid4().hex
    campaign, framework_id, version_id = await create_confirmed_campaign(marker)
    hermes = FakeHermesAdapter(
        [], media_plan_results=[media_plan_result(summary="A 草案", complete=False)]
    )
    async with SessionFactory() as session:
        service = MediaPlanService(MediaPlanRepository(session), hermes)
        snapshot = await service.generate(
            campaign.id,
            StageCode.A,
            confirmation_inputs=ConfirmationInputs(),
            idempotency_key=f"generate-{marker}",
        )
        with pytest.raises(StageNotOpenError):
            await service.generate(
                campaign.id,
                StageCode.B,
                confirmation_inputs=ConfirmationInputs(),
                idempotency_key=f"generate-b-{marker}",
            )
        with pytest.raises(StageNotOpenError):
            await service.generate(
                campaign.id,
                StageCode.C,
                confirmation_inputs=ConfirmationInputs(),
                idempotency_key=f"generate-c-{marker}",
            )

    assert snapshot.plan.source_strategy_framework_id == framework_id
    assert snapshot.plan.source_strategy_version_id == version_id
    assert snapshot.current_version.version == 1
    assert snapshot.current_version.missing_confirmation_fields
    assert snapshot.workflow.state == "MEDIA_PLAN_PENDING_CONFIRMATION"


@pytest.mark.integration
async def test_parameter_and_instruction_revisions_keep_full_history_and_session() -> None:
    marker = uuid4().hex
    campaign, _, _ = await create_confirmed_campaign(marker)
    async with SessionFactory() as session:
        initial_service = MediaPlanService(
            MediaPlanRepository(session),
            FakeHermesAdapter(
                [], media_plan_results=[media_plan_result(summary="V1", complete=False)]
            ),
        )
        await initial_service.generate(
            campaign.id,
            StageCode.A,
            confirmation_inputs=ConfirmationInputs(),
            idempotency_key=f"generate-revise-{marker}",
        )

    hermes = FakeHermesAdapter(
        [],
        media_plan_results=[
            media_plan_result(summary="V2 补参", complete=True),
            media_plan_result(summary="V3 优化", complete=True),
        ],
    )
    async with SessionFactory() as session:
        service = MediaPlanService(MediaPlanRepository(session), hermes)
        version_2 = await service.revise(
            campaign.id,
            StageCode.A,
            expected_version=1,
            instruction=None,
            confirmation_inputs=complete_inputs(),
            actor_id="user-1",
            idempotency_key=f"params-{marker}",
        )
        assert version_2.current_version.version == 2
        version_3 = await service.revise(
            campaign.id,
            StageCode.A,
            expected_version=2,
            instruction="强化核心人群测试",
            confirmation_inputs=None,
            actor_id="user-1",
            idempotency_key=f"instruction-{marker}",
        )

    assert version_3.current_version.version == 3
    assert [item.status for item in version_3.versions] == [
        "SUPERSEDED",
        "SUPERSEDED",
        "PENDING_CONFIRMATION",
    ]
    assert version_3.versions[2].parent_version_id == version_3.versions[1].id
    assert hermes.media_plan_requests[0].stored_session_id is not None
    assert hermes.media_plan_requests[1].stored_session_id is not None
    assert hermes.media_plan_requests[1].current_plan["plan_summary"] == "V2 补参"
    assert version_3.current_version.confirmation_inputs["target_roi"] == "1.50"


@pytest.mark.integration
async def test_failed_revision_can_retry_same_key_but_changed_parameters_conflict() -> None:
    marker = uuid4().hex
    campaign, _, _ = await create_confirmed_campaign(marker)
    async with SessionFactory() as session:
        await MediaPlanService(
            MediaPlanRepository(session),
            FakeHermesAdapter(
                [], media_plan_results=[media_plan_result(summary="V1", complete=False)]
            ),
        ).generate(
            campaign.id,
            StageCode.A,
            confirmation_inputs=ConfirmationInputs(),
            idempotency_key=f"generate-retry-{marker}",
        )

    key = f"retry-{marker}"
    async with SessionFactory() as session:
        failing = MediaPlanService(
            MediaPlanRepository(session),
            FakeHermesAdapter([], media_plan_results=[RuntimeError("gateway down")]),
        )
        with pytest.raises(Exception, match="temporarily unavailable"):
            await failing.revise(
                campaign.id,
                StageCode.A,
                expected_version=1,
                instruction="修改节奏",
                confirmation_inputs=None,
                actor_id="user-1",
                idempotency_key=key,
            )

    async with SessionFactory() as session:
        service = MediaPlanService(
            MediaPlanRepository(session),
            FakeHermesAdapter(
                [], media_plan_results=[media_plan_result(summary="V2", complete=False)]
            ),
        )
        with pytest.raises(MediaPlanIdempotencyConflictError):
            await service.revise(
                campaign.id,
                StageCode.A,
                expected_version=1,
                instruction="不同意见",
                confirmation_inputs=None,
                actor_id="user-2",
                idempotency_key=key,
            )
        retried = await service.revise(
            campaign.id,
            StageCode.A,
            expected_version=1,
            instruction="修改节奏",
            confirmation_inputs=None,
            actor_id="user-1",
            idempotency_key=key,
        )

    assert retried.current_version.version == 2


@pytest.mark.integration
async def test_failed_initial_generation_reuses_plan_and_protects_idempotency_key() -> None:
    marker = uuid4().hex
    key = f"initial-retry-{marker}"
    campaign, _, _ = await create_confirmed_campaign(marker)
    async with SessionFactory() as session:
        failing = MediaPlanService(
            MediaPlanRepository(session),
            FakeHermesAdapter([], media_plan_results=[RuntimeError("gateway down")]),
        )
        with pytest.raises(ExternalMediaPlanServiceError):
            await failing.generate(
                campaign.id,
                StageCode.A,
                confirmation_inputs=ConfirmationInputs(),
                idempotency_key=key,
            )

    async with SessionFactory() as session:
        service = MediaPlanService(
            MediaPlanRepository(session),
            FakeHermesAdapter(
                [], media_plan_results=[media_plan_result(summary="V1", complete=False)]
            ),
        )
        retried = await service.generate(
            campaign.id,
            StageCode.A,
            confirmation_inputs=ConfirmationInputs(),
            idempotency_key=key,
        )
        with pytest.raises(MediaPlanIdempotencyConflictError):
            await service.generate(
                campaign.id,
                StageCode.A,
                confirmation_inputs=complete_inputs(),
                idempotency_key=key,
            )

    assert retried.current_version.version == 1
    assert len(retried.versions) == 1


@pytest.mark.integration
async def test_changed_confirmed_strategy_during_generation_rejects_stale_output() -> None:
    marker = uuid4().hex
    campaign, framework_id, _ = await create_confirmed_campaign(marker)

    class StrategyChangingHermes(FakeHermesAdapter):
        async def run_media_plan_generation(self, request):
            async with SessionFactory() as mutation_session:
                framework = await mutation_session.get(
                    MarketingStrategyFramework, framework_id
                )
                replacement = MarketingStrategyVersion(
                    framework_id=framework_id,
                    version=2,
                    status="CONFIRMED",
                    content=valid_strategy_payload(),
                )
                mutation_session.add(replacement)
                await mutation_session.flush()
                framework.current_version_id = replacement.id
                framework.confirmed_version_id = replacement.id
                await mutation_session.commit()
            return await super().run_media_plan_generation(request)

    hermes = StrategyChangingHermes(
        [], media_plan_results=[media_plan_result(summary="过期计划", complete=False)]
    )
    async with SessionFactory() as session:
        service = MediaPlanService(MediaPlanRepository(session), hermes)
        with pytest.raises(StaleStrategySourceError):
            await service.generate(
                campaign.id,
                StageCode.A,
                confirmation_inputs=ConfirmationInputs(),
                idempotency_key=f"stale-source-{marker}",
            )


@pytest.mark.integration
async def test_reopened_strategy_status_invalidates_existing_media_plan_source() -> None:
    marker = uuid4().hex
    campaign, framework_id, _ = await create_confirmed_campaign(marker)
    async with SessionFactory() as session:
        service = MediaPlanService(
            MediaPlanRepository(session),
            FakeHermesAdapter(
                [], media_plan_results=[media_plan_result(summary="V1", complete=False)]
            ),
        )
        await service.generate(
            campaign.id,
            StageCode.A,
            confirmation_inputs=ConfirmationInputs(),
            idempotency_key=f"generate-reopened-{marker}",
        )

    async with SessionFactory() as session:
        framework = await session.get(MarketingStrategyFramework, framework_id)
        framework.status = "GENERATING"
        await session.commit()

    async with SessionFactory() as session:
        service = MediaPlanService(
            MediaPlanRepository(session), FakeHermesAdapter([])
        )
        with pytest.raises(StaleStrategySourceError):
            await service.confirm(
                campaign.id,
                StageCode.A,
                expected_version=1,
                actor_id="approver",
                idempotency_key=f"confirm-reopened-{marker}",
            )


@pytest.mark.integration
async def test_gate_1_5_requires_complete_current_version_and_binds_approval() -> None:
    marker = uuid4().hex
    campaign, _, _ = await create_confirmed_campaign(marker)
    async with SessionFactory() as session:
        service = MediaPlanService(
            MediaPlanRepository(session),
            FakeHermesAdapter(
                [],
                media_plan_results=[
                    media_plan_result(summary="V1", complete=False),
                    media_plan_result(summary="V2", complete=True),
                ],
            ),
        )
        await service.generate(
            campaign.id,
            StageCode.A,
            confirmation_inputs=ConfirmationInputs(),
            idempotency_key=f"generate-confirm-{marker}",
        )
        with pytest.raises(MediaPlanNotConfirmableError):
            await service.confirm(
                campaign.id,
                StageCode.A,
                expected_version=1,
                actor_id="approver",
                idempotency_key=f"incomplete-{marker}",
            )
        complete = await service.revise(
            campaign.id,
            StageCode.A,
            expected_version=1,
            instruction=None,
            confirmation_inputs=complete_inputs(),
            actor_id="user-1",
            idempotency_key=f"complete-{marker}",
        )
        with pytest.raises(StaleMediaPlanVersionError):
            await service.confirm(
                campaign.id,
                StageCode.A,
                expected_version=1,
                actor_id="approver",
                idempotency_key=f"stale-confirm-{marker}",
            )
        confirmed = await service.confirm(
            campaign.id,
            StageCode.A,
            expected_version=2,
            actor_id="approver",
            idempotency_key=f"confirm-{marker}",
        )

    assert complete.current_version.version == 2
    assert confirmed.workflow.state == "MEDIA_PLAN_CONFIRMED"
    assert confirmed.plan.confirmed_version_id == confirmed.current_version.id
    assert confirmed.current_version.status == "CONFIRMED"
    assert confirmed.decisions[-1].gate_code == "GATE_1_5"
    assert confirmed.decisions[-1].resource_version_id == confirmed.current_version.id
