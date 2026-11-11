from pydantic import BaseModel, ConfigDict

from app.hermes.contracts import VideoScriptResult


class ScriptValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    field: str
    message: str


class ScriptValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    errors: list[ScriptValidationIssue]


def _issue(code: str, field: str, message: str) -> ScriptValidationIssue:
    return ScriptValidationIssue(code=code, field=field, message=message)


def validate_video_script(
    result: VideoScriptResult, expected_slot: dict
) -> ScriptValidationResult:
    """校验 LLM 脚本与被领取槽位及确定性时间轴的一致性。"""

    errors: list[ScriptValidationIssue] = []
    if result.creative_slot_id != expected_slot.get("id"):
        errors.append(
            _issue(
                "CREATIVE_SLOT_ID_MISMATCH",
                "creative_slot_id",
                "script creative slot must match the claimed job",
            )
        )
    if result.creative_type != expected_slot.get("format"):
        errors.append(
            _issue(
                "CREATIVE_TYPE_MISMATCH",
                "creative_type",
                "script creative type must match the media-plan slot",
            )
        )

    scene_numbers = [scene.scene_no for scene in result.scenes]
    if scene_numbers != list(range(1, len(result.scenes) + 1)):
        errors.append(
            _issue(
                "INVALID_SCENE_SEQUENCE",
                "scenes",
                "scene numbers must be unique and contiguous from 1",
            )
        )

    previous_end = 0
    for scene in result.scenes:
        if scene.start_second > previous_end:
            errors.append(
                _issue(
                    "SCENE_TIMELINE_GAP",
                    f"scenes.{scene.scene_no}.start_second",
                    "scene timeline must not contain gaps",
                )
            )
        elif scene.start_second < previous_end:
            errors.append(
                _issue(
                    "SCENE_TIMELINE_OVERLAP",
                    f"scenes.{scene.scene_no}.start_second",
                    "scene timeline must not overlap",
                )
            )
        previous_end = scene.end_second

    if result.scenes[-1].end_second != result.duration_seconds:
        errors.append(
            _issue(
                "SCRIPT_DURATION_MISMATCH",
                "duration_seconds",
                "script duration must equal the final scene end",
            )
        )

    return ScriptValidationResult(valid=not errors, errors=errors)
