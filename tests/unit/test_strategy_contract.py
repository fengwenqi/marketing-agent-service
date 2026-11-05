from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.hermes.contracts import MarketingStrategyResult


def valid_strategy_payload() -> dict:
    stage_specs = [
        ("A", "起量赛马", 50, 15, 15, 9, 6),
        ("B", "收敛优化", 30, 5, 5, 3, 2),
        ("C", "集中收口", 20, 1, 2, 1, 1),
    ]
    stages = []
    for code, name, ratio, count_min, count_max, oral, short in stage_specs:
        stages.append(
            {
                "code": code,
                "name": name,
                "objective": f"{name}目标",
                "lifecycle": "预热期" if code == "A" else "增长期",
                "tagline": f"{name}定位",
                "communication_theme": "徕卡影像旗舰",
                "target_audience": ["影像爱好者"],
                "core_selling_points": ["专业影像"],
                "content_directions": ["影像实拍", "场景体验"],
                "material_plan": {
                    "target_count_min": count_min,
                    "target_count_max": count_max,
                    "oral_video_count": oral,
                    "short_video_count": short,
                },
                "delivery": {
                    "budget_ratio": ratio,
                    "channel": "douyin",
                    "launch_form": "qianchuan_universal_live_room",
                    "bidding": "直播间下单 oCPM · ROI 出价",
                    "targeting": "A阶段全托，后续按数据收窄",
                },
                "success_criteria": ["消耗速度", "成交 ROI"],
                "next_stage_inputs": ["素材表现", "人群画像"],
                "content_brief": {
                    "stage": code,
                    "communication_theme": "徕卡影像旗舰",
                    "target_audience": ["影像爱好者"],
                    "core_selling_points": ["专业影像"],
                    "content_focus": "真实影像场景",
                    "content_directions": ["影像实拍"],
                    "channel": "douyin",
                    "launch_object": "live_room",
                    "bidding": "ROI 出价",
                    "material_package": f"{count_min}-{count_max} 条",
                    "stage_inputs": ["已确认营销目标"],
                    "execution_progression": "按阶段数据推进",
                    "conversion_path": ["内容曝光", "进入直播间", "下单"],
                    "rhythm": "随开播时段",
                    "core_metrics": ["消耗", "成交 ROI"],
                },
            }
        )
    return {
        "schema_version": "1.0",
        "framework_summary": "A 起量、B 收敛、C 收口的三阶段框架",
        "rationale": "先验证方向，再集中预算",
        "stages": stages,
    }


def test_strategy_contract_accepts_complete_abc_framework() -> None:
    result = MarketingStrategyResult.model_validate(valid_strategy_payload())

    assert [stage.code for stage in result.stages] == ["A", "B", "C"]
    assert result.stages[2].material_plan.target_count_min == 1
    assert result.stages[2].material_plan.target_count_max == 2


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["stages"].pop(),
        lambda payload: payload["stages"].reverse(),
        lambda payload: payload["stages"][1].update(code="A"),
        lambda payload: payload["stages"][0]["delivery"].update(budget_ratio=40),
        lambda payload: payload["stages"][0]["delivery"].update(channel="xiaohongshu"),
        lambda payload: payload["stages"][0]["delivery"].update(launch_form="product_card"),
        lambda payload: payload["stages"][2]["material_plan"].update(
            target_count_min=2, target_count_max=3
        ),
        lambda payload: payload.update(unexpected="not allowed"),
    ],
)
def test_strategy_contract_rejects_invalid_business_invariants(mutate) -> None:
    payload = deepcopy(valid_strategy_payload())
    mutate(payload)

    with pytest.raises(ValidationError):
        MarketingStrategyResult.model_validate(payload)
