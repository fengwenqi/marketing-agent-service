from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.hermes.contracts import VideoScriptResult


def valid_video_script_payload() -> dict:
    return {
        "schema_version": "1.0",
        "creative_slot_id": "oral-1",
        "creative_type": "oral_video",
        "script_title": "小米17 Pro影像能力口播",
        "duration_seconds": 30,
        "target_audience": "关注手机影像的换机用户",
        "core_message": "旗舰影像能力让日常记录更轻松",
        "hook": "手机也能拍出电影感吗？",
        "scenes": [
            {
                "scene_no": 1,
                "start_second": 0,
                "end_second": 10,
                "visual_description": "主播展示手机和样张",
                "shot_type": "medium",
                "voiceover": "先看这张随手拍。",
                "subtitle": "随手拍也有电影感",
            },
            {
                "scene_no": 2,
                "start_second": 10,
                "end_second": 30,
                "visual_description": "切换夜景与人像样张",
                "shot_type": "close_up",
                "voiceover": "夜景和人像都能保留丰富细节。",
                "subtitle": "夜景人像清晰自然",
            },
        ],
        "cta": "进入直播间了解更多",
        "compliance_notes": ["避免绝对化效果承诺"],
        "production_notes": ["样张使用经授权素材"],
    }


def test_valid_video_script_contract_is_accepted() -> None:
    result = VideoScriptResult.model_validate(valid_video_script_payload())

    assert result.creative_slot_id == "oral-1"
    assert result.duration_seconds == 30
    assert len(result.scenes) == 2


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(extra="forbidden"),
        lambda payload: payload.update(creative_type="image"),
        lambda payload: payload.update(script_title=""),
        lambda payload: payload.update(hook=""),
        lambda payload: payload.update(core_message=""),
        lambda payload: payload.update(cta=""),
        lambda payload: payload.update(scenes=[]),
        lambda payload: payload["scenes"][0].update(start_second=-1),
    ],
)
def test_invalid_video_script_contract_is_rejected(mutate) -> None:
    payload = deepcopy(valid_video_script_payload())
    mutate(payload)

    with pytest.raises(ValidationError):
        VideoScriptResult.model_validate(payload)
