from copy import deepcopy
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.hermes.contracts import MarketingStageStrategy, StageMediaPlanResult
from app.media_plans.validation import ConfirmationInputs, validate_media_plan
from tests.unit.test_media_plan_contract import valid_media_plan_payload
from tests.unit.test_strategy_contract import valid_strategy_payload


def A_strategy() -> MarketingStageStrategy:
    return MarketingStageStrategy.model_validate(valid_strategy_payload()["stages"][0])


def full_inputs(
    *,
    total: str = "10000.00",
    start: date = date(2026, 11, 10),
    end: date = date(2026, 11, 20),
) -> ConfirmationInputs:
    return ConfirmationInputs(
        campaign_total_budget=Decimal(total),
        stage_start_date=start,
        stage_end_date=end,
        target_roi=Decimal("1.50"),
    )


def complete_plan(
    *, total: str = "10000.00", start: str = "2026-11-10", end: str = "2026-11-20"
) -> StageMediaPlanResult:
    payload = valid_media_plan_payload()
    stage_budget = (Decimal(total) * Decimal("0.50")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    payload["delivery_objective"]["target_roi"] = "1.50"
    payload["budget_plan"]["stage_budget"] = str(stage_budget)
    payload["budget_plan"]["units"][0]["amount"] = str(stage_budget)
    payload["schedule"][0]["start_date"] = start
    payload["schedule"][0]["end_date"] = end
    return StageMediaPlanResult.model_validate(payload)


def test_draft_reports_missing_confirmation_fields() -> None:
    result = validate_media_plan(
        StageMediaPlanResult.model_validate(valid_media_plan_payload()),
        ConfirmationInputs(),
        A_strategy(),
    )

    assert result.is_confirmable is False
    assert result.missing_fields == [
        "campaign_total_budget",
        "stage_start_date",
        "stage_end_date",
        "target_roi",
    ]


def test_complete_A_plan_is_confirmable() -> None:
    result = validate_media_plan(complete_plan(), full_inputs(), A_strategy())

    assert result.is_confirmable is True
    assert result.errors == []
    assert result.computed["stage_budget"] == "5000.00"


def test_budget_allocation_assigns_rounding_remainder_deterministically() -> None:
    payload = complete_plan(total="100.01").model_dump(mode="json")
    payload["budget_plan"]["units"] = [
        {
            "id": "unit-1",
            "name": "第一单元",
            "allocation_ratio": "33.33",
            "amount": "16.67",
            "audience_package_ids": ["audience-1"],
        },
        {
            "id": "unit-2",
            "name": "第二单元",
            "allocation_ratio": "33.33",
            "amount": "16.67",
            "audience_package_ids": ["audience-1"],
        },
        {
            "id": "unit-3",
            "name": "第三单元",
            "allocation_ratio": "33.34",
            "amount": "16.67",
            "audience_package_ids": ["audience-1"],
        },
    ]
    payload["schedule"][0]["budget_unit_ids"] = ["unit-1", "unit-2", "unit-3"]
    for cell in payload["testing_matrix"]:
        cell["budget_unit_id"] = "unit-1"

    result = validate_media_plan(
        StageMediaPlanResult.model_validate(payload),
        full_inputs(total="100.01"),
        A_strategy(),
    )

    assert result.is_confirmable is True
    assert result.computed["stage_budget"] == "50.01"
    assert result.computed["budget_unit_amounts"] == {
        "unit-1": "16.67",
        "unit-2": "16.67",
        "unit-3": "16.67",
    }


def test_invalid_date_order_is_rejected() -> None:
    result = validate_media_plan(
        complete_plan(),
        full_inputs(start=date(2026, 11, 20), end=date(2026, 11, 10)),
        A_strategy(),
    )

    assert "INVALID_DATE_RANGE" in [error.code for error in result.errors]


def test_schedule_across_year_boundary_is_validated_as_dates() -> None:
    result = validate_media_plan(
        complete_plan(start="2026-12-30", end="2027-01-03"),
        full_inputs(start=date(2026, 12, 30), end=date(2027, 1, 3)),
        A_strategy(),
    )

    assert result.is_confirmable is True


def test_schedule_outside_confirmed_range_is_rejected() -> None:
    result = validate_media_plan(
        complete_plan(start="2026-11-09", end="2026-11-20"),
        full_inputs(),
        A_strategy(),
    )

    assert "SCHEDULE_OUTSIDE_STAGE_RANGE" in [error.code for error in result.errors]


def test_unknown_audience_budget_and_creative_references_are_rejected() -> None:
    payload = deepcopy(complete_plan().model_dump(mode="json"))
    payload["testing_matrix"][0]["audience_package_id"] = "missing-audience"
    payload["testing_matrix"][0]["budget_unit_id"] = "missing-unit"
    payload["testing_matrix"][0]["creative_slot_ids"].append("missing-slot")

    result = validate_media_plan(
        StageMediaPlanResult.model_validate(payload), full_inputs(), A_strategy()
    )

    codes = [error.code for error in result.errors]
    assert "UNKNOWN_AUDIENCE_REFERENCE" in codes
    assert "UNKNOWN_BUDGET_UNIT_REFERENCE" in codes
    assert "UNKNOWN_CREATIVE_SLOT_REFERENCE" in codes
