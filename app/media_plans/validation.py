from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field

from app.hermes.contracts import MarketingStageStrategy, StageMediaPlanResult

CENT = Decimal("0.01")


class ConfirmationInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_total_budget: Decimal | None = Field(default=None, gt=0)
    stage_start_date: date | None = None
    stage_end_date: date | None = None
    target_roi: Decimal | None = Field(default=None, gt=0)


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    field: str
    message: str


class PlanValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_confirmable: bool
    missing_fields: list[str]
    errors: list[ValidationIssue]
    computed: dict[str, object]


def _issue(code: str, field: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, field=field, message=message)


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _allocate_budget(
    stage_budget: Decimal, ratios: dict[str, Decimal]
) -> dict[str, Decimal]:
    """按分向下取分，再按单元 ID 顺序分配余下的分，保证结果稳定。"""

    ordered_ids = sorted(ratios)
    allocations = {
        unit_id: (stage_budget * ratios[unit_id] / Decimal("100")).quantize(
            CENT, rounding=ROUND_DOWN
        )
        for unit_id in ordered_ids
    }
    remaining_cents = int((stage_budget - sum(allocations.values())) / CENT)
    for index in range(remaining_cents):
        unit_id = ordered_ids[index % len(ordered_ids)]
        allocations[unit_id] += CENT
    return allocations


def validate_media_plan(
    result: StageMediaPlanResult,
    inputs: ConfirmationInputs,
    strategy: MarketingStageStrategy,
) -> PlanValidationResult:
    missing_fields = [
        field
        for field in (
            "campaign_total_budget",
            "stage_start_date",
            "stage_end_date",
            "target_roi",
        )
        if getattr(inputs, field) is None
    ]
    errors: list[ValidationIssue] = []
    computed: dict[str, object] = {}

    if strategy.code != "A" or strategy.delivery.budget_ratio != 50:
        errors.append(
            _issue(
                "INVALID_SOURCE_STRATEGY",
                "strategy",
                "A stage strategy with a 50% budget ratio is required",
            )
        )

    unit_ids = [unit.id for unit in result.budget_plan.units]
    if len(unit_ids) != len(set(unit_ids)):
        errors.append(
            _issue("DUPLICATE_BUDGET_UNIT", "budget_plan.units", "unit ids must be unique")
        )
    ratios = {unit.id: Decimal(unit.allocation_ratio) for unit in result.budget_plan.units}
    if sum(ratios.values()) != Decimal("100"):
        errors.append(
            _issue(
                "INVALID_BUDGET_RATIO_TOTAL",
                "budget_plan.units",
                "budget allocation ratios must total 100",
            )
        )

    if inputs.campaign_total_budget is not None:
        stage_budget = _money(inputs.campaign_total_budget * Decimal("0.50"))
        computed["stage_budget"] = f"{stage_budget:.2f}"
        reported_stage_budget = _decimal(result.budget_plan.stage_budget)
        if reported_stage_budget != stage_budget:
            errors.append(
                _issue(
                    "STAGE_BUDGET_MISMATCH",
                    "budget_plan.stage_budget",
                    "stage budget must equal 50% of campaign total budget",
                )
            )
        if ratios and sum(ratios.values()) == Decimal("100"):
            allocations = _allocate_budget(stage_budget, ratios)
            computed["budget_unit_amounts"] = {
                key: f"{value:.2f}" for key, value in allocations.items()
            }
            for unit in result.budget_plan.units:
                if _decimal(unit.amount) != allocations[unit.id]:
                    errors.append(
                        _issue(
                            "BUDGET_UNIT_AMOUNT_MISMATCH",
                            f"budget_plan.units.{unit.id}.amount",
                            "budget unit amount does not match deterministic allocation",
                        )
                    )

    if inputs.target_roi is not None:
        reported_roi = _decimal(result.delivery_objective.target_roi)
        if reported_roi != inputs.target_roi:
            errors.append(
                _issue(
                    "TARGET_ROI_MISMATCH",
                    "delivery_objective.target_roi",
                    "plan ROI must match the user-confirmed ROI",
                )
            )

    if (
        inputs.stage_start_date is not None
        and inputs.stage_end_date is not None
        and inputs.stage_start_date > inputs.stage_end_date
    ):
        errors.append(
            _issue(
                "INVALID_DATE_RANGE",
                "stage_end_date",
                "stage end date cannot be before start date",
            )
        )

    if inputs.stage_start_date is not None and inputs.stage_end_date is not None:
        for entry in result.schedule:
            if entry.start_date is None or entry.end_date is None:
                errors.append(
                    _issue(
                        "MISSING_SCHEDULE_DATE",
                        f"schedule.{entry.id}",
                        "schedule dates are required after stage dates are confirmed",
                    )
                )
                continue
            entry_start = date.fromisoformat(entry.start_date)
            entry_end = date.fromisoformat(entry.end_date)
            if (
                entry_start > entry_end
                or entry_start < inputs.stage_start_date
                or entry_end > inputs.stage_end_date
            ):
                errors.append(
                    _issue(
                        "SCHEDULE_OUTSIDE_STAGE_RANGE",
                        f"schedule.{entry.id}",
                        "schedule must stay inside the confirmed stage range",
                    )
                )

    audience_ids = {item.id for item in result.audience_packages}
    budget_unit_ids = set(unit_ids)
    creative_slot_ids = {item.id for item in result.creative_slots}
    for unit in result.budget_plan.units:
        if not set(unit.audience_package_ids).issubset(audience_ids):
            errors.append(
                _issue(
                    "UNKNOWN_AUDIENCE_REFERENCE",
                    f"budget_plan.units.{unit.id}.audience_package_ids",
                    "budget unit references an unknown audience package",
                )
            )
    for entry in result.schedule:
        if not set(entry.budget_unit_ids).issubset(budget_unit_ids):
            errors.append(
                _issue(
                    "UNKNOWN_BUDGET_UNIT_REFERENCE",
                    f"schedule.{entry.id}.budget_unit_ids",
                    "schedule references an unknown budget unit",
                )
            )
    for cell in result.testing_matrix:
        if cell.audience_package_id not in audience_ids:
            errors.append(
                _issue(
                    "UNKNOWN_AUDIENCE_REFERENCE",
                    f"testing_matrix.{cell.id}.audience_package_id",
                    "test cell references an unknown audience package",
                )
            )
        if cell.budget_unit_id not in budget_unit_ids:
            errors.append(
                _issue(
                    "UNKNOWN_BUDGET_UNIT_REFERENCE",
                    f"testing_matrix.{cell.id}.budget_unit_id",
                    "test cell references an unknown budget unit",
                )
            )
        if not set(cell.creative_slot_ids).issubset(creative_slot_ids):
            errors.append(
                _issue(
                    "UNKNOWN_CREATIVE_SLOT_REFERENCE",
                    f"testing_matrix.{cell.id}.creative_slot_ids",
                    "test cell references an unknown creative slot",
                )
            )

    return PlanValidationResult(
        is_confirmable=not missing_fields and not errors,
        missing_fields=missing_fields,
        errors=errors,
        computed=computed,
    )
