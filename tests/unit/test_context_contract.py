import pytest
from pydantic import ValidationError

from app.campaigns.domain import CampaignEvent
from app.hermes.contracts import MarketingContextResult
from app.hermes.result_mapper import map_context_result


def context_result(*, status: str, next_action: str) -> dict:
    return {
        "schema_version": "1.0",
        "status": status,
        "generation_mode": None,
        "warnings_accepted": False,
        "original_request": "推广小米17 Pro",
        "goal": {"product": "小米17 Pro"},
        "product_profile": {"product_key": "xiaomi-17-pro"},
        "enterprise_profile": {},
        "marketing_evidence": {},
        "missing_required": [],
        "missing_optional": [],
        "conflicts": [],
        "field_sources": {
            "goal.product": {"status": "user_provided", "source": "用户原话"}
        },
        "evidence": [],
        "next_action": next_action,
        "next_question": None,
        "persistence": {"saved": False, "note": "本地第一版未接入数据库"},
    }


def test_blocked_context_maps_to_blocked_event() -> None:
    payload = context_result(status="BLOCKED", next_action="询问目标人群")
    payload["missing_required"] = ["target_audience"]
    result = MarketingContextResult.model_validate(payload)

    assert map_context_result(result) is CampaignEvent.CONTEXT_BLOCKED


def test_ready_context_maps_to_ready_event() -> None:
    payload = context_result(status="READY", next_action="进入营销计划")
    payload["generation_mode"] = "DATA_DRIVEN"
    result = MarketingContextResult.model_validate(payload)

    assert map_context_result(result) is CampaignEvent.CONTEXT_READY


def test_unknown_status_is_rejected() -> None:
    payload = context_result(status="DONE", next_action="无效状态")
    with pytest.raises(ValidationError):
        MarketingContextResult.model_validate(payload)


def test_unknown_top_level_field_is_rejected() -> None:
    payload = context_result(status="BLOCKED", next_action="继续采集")
    payload["unexpected"] = True

    with pytest.raises(ValidationError):
        MarketingContextResult.model_validate(payload)


def test_field_source_status_shorthand_is_normalized() -> None:
    payload = context_result(status="BLOCKED", next_action="继续采集")
    payload["field_sources"] = {"goal.product": "unresolved"}

    result = MarketingContextResult.model_validate(payload)

    assert result.field_sources["goal.product"].status == "unresolved"
    assert result.field_sources["goal.product"].source is None

