from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.hermes.contracts import StageMediaPlanResult


def valid_media_plan_payload() -> dict:
    slots = [
        {
            "id": f"oral-{index}",
            "format": "oral_video",
            "direction": f"口播方向 {index}",
            "test_variable": "selling_point",
            "priority": index,
        }
        for index in range(1, 10)
    ] + [
        {
            "id": f"short-{index}",
            "format": "short_video",
            "direction": f"短视频方向 {index}",
            "test_variable": "opening",
            "priority": index + 9,
        }
        for index in range(1, 7)
    ]
    return {
        "schema_version": "1.0",
        "stage_code": "A",
        "plan_summary": "A 阶段起量赛马投放草案",
        "planning_assumptions": ["预算待用户确认"],
        "delivery_objective": {
            "channel": "douyin",
            "platform": "qianchuan",
            "launch_target": "live_room",
            "objective": "获取有效销售线索",
            "target_roi": None,
        },
        "audience_packages": [
            {
                "id": "audience-1",
                "name": "核心人群",
                "description": "高意向换机用户",
                "targeting": ["智能手机", "数码科技"],
            }
        ],
        "budget_plan": {
            "stage_budget": None,
            "units": [
                {
                    "id": "unit-1",
                    "name": "核心人群测试",
                    "allocation_ratio": "100",
                    "amount": None,
                    "audience_package_ids": ["audience-1"],
                }
            ],
        },
        "schedule": [
            {
                "id": "schedule-1",
                "start_date": None,
                "end_date": None,
                "budget_unit_ids": ["unit-1"],
                "description": "起量测试",
            }
        ],
        "bidding_strategy": {
            "method": "自动出价",
            "optimization_goal": "直播间线索",
            "bid_guidance": "按转化成本动态调整",
        },
        "testing_matrix": [
            {
                "id": "test-1",
                "audience_package_id": "audience-1",
                "creative_slot_ids": [slot["id"] for slot in slots],
                "budget_unit_id": "unit-1",
            }
        ],
        "creative_slots": slots,
        "monitoring_metrics": [
            {"name": "ROI", "target": "待确认", "window": "daily"}
        ],
        "optimization_rules": [
            {"condition": "连续低于目标", "action": "降低预算并替换素材"}
        ],
        "risk_controls": [
            {"risk": "冷启动消耗慢", "mitigation": "扩展相似人群"}
        ],
    }


def test_valid_A_stage_draft_is_accepted() -> None:
    result = StageMediaPlanResult.model_validate(valid_media_plan_payload())

    assert result.stage_code == "A"
    assert len(result.creative_slots) == 15


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(extra="forbidden"),
        lambda payload: payload.update(stage_code="B"),
        lambda payload: payload["delivery_objective"].update(channel="xiaohongshu"),
        lambda payload: payload["delivery_objective"].update(platform="ocean_engine"),
        lambda payload: payload["delivery_objective"].update(launch_target="product"),
        lambda payload: payload["creative_slots"][1].update(id="oral-1"),
        lambda payload: payload["creative_slots"].pop(),
        lambda payload: payload["creative_slots"][0].update(format="short_video"),
        lambda payload: payload["audience_packages"].append(
            deepcopy(payload["audience_packages"][0])
        ),
        lambda payload: payload["schedule"].append(deepcopy(payload["schedule"][0])),
        lambda payload: payload["testing_matrix"].append(
            deepcopy(payload["testing_matrix"][0])
        ),
    ],
)
def test_invalid_A_stage_contract_is_rejected(mutate) -> None:
    payload = deepcopy(valid_media_plan_payload())
    mutate(payload)

    with pytest.raises(ValidationError):
        StageMediaPlanResult.model_validate(payload)
