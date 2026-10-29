import pytest
from pydantic import ValidationError

from app.campaigns.domain import CampaignEvent
from app.hermes.contracts import MarketingContextResult
from app.hermes.result_mapper import map_context_result


def test_blocked_context_maps_to_blocked_event() -> None:
    result = MarketingContextResult.model_validate(
        {
            "status": "BLOCKED",
            "generation_mode": None,
            "goal": {"product": "小米17 Pro"},
            "product": None,
            "missing_required": ["target_audience"],
            "missing_optional": [],
            "conflicts": [],
            "evidence": [],
            "warnings_accepted": False,
            "next_action": "询问目标人群",
        }
    )

    assert map_context_result(result) is CampaignEvent.CONTEXT_BLOCKED


def test_ready_context_maps_to_ready_event() -> None:
    result = MarketingContextResult.model_validate(
        {
            "status": "READY",
            "generation_mode": "DATA_DRIVEN",
            "goal": {"product": "小米17 Pro"},
            "product": {"product_key": "xiaomi-17-pro"},
            "missing_required": [],
            "missing_optional": [],
            "conflicts": [],
            "evidence": [],
            "warnings_accepted": False,
            "next_action": "进入营销计划",
        }
    )

    assert map_context_result(result) is CampaignEvent.CONTEXT_READY


def test_unknown_status_is_rejected() -> None:
    with pytest.raises(ValidationError):
        MarketingContextResult.model_validate(
            {
                "status": "DONE",
                "goal": {},
                "missing_required": [],
                "missing_optional": [],
                "conflicts": [],
                "evidence": [],
                "warnings_accepted": False,
                "next_action": "",
            }
        )

