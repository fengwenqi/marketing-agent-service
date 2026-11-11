from copy import deepcopy

from app.hermes.contracts import VideoScriptResult
from app.video_scripts.validation import validate_video_script
from tests.unit.test_video_script_contract import valid_video_script_payload

EXPECTED_SLOT = {"id": "oral-1", "format": "oral_video"}


def validate(payload: dict, expected_slot: dict | None = None):
    return validate_video_script(
        VideoScriptResult.model_validate(payload), expected_slot or EXPECTED_SLOT
    )


def test_valid_video_script_matches_slot_and_timeline() -> None:
    result = validate(valid_video_script_payload())

    assert result.valid is True
    assert result.errors == []


def test_slot_identity_and_type_must_match_claimed_job() -> None:
    payload = valid_video_script_payload()
    payload["creative_slot_id"] = "oral-2"
    payload["creative_type"] = "short_video"

    result = validate(payload)

    assert result.valid is False
    assert {error.code for error in result.errors} == {
        "CREATIVE_SLOT_ID_MISMATCH",
        "CREATIVE_TYPE_MISMATCH",
    }


def test_scene_numbers_must_be_unique_and_contiguous() -> None:
    payload = valid_video_script_payload()
    payload["scenes"][1]["scene_no"] = 1

    result = validate(payload)

    assert result.valid is False
    assert [error.code for error in result.errors] == ["INVALID_SCENE_SEQUENCE"]


def test_scene_timeline_rejects_gap_overlap_and_wrong_duration() -> None:
    gap = deepcopy(valid_video_script_payload())
    gap["scenes"][1]["start_second"] = 11
    overlap = deepcopy(valid_video_script_payload())
    overlap["scenes"][1]["start_second"] = 9
    wrong_duration = deepcopy(valid_video_script_payload())
    wrong_duration["duration_seconds"] = 31

    assert {error.code for error in validate(gap).errors} == {"SCENE_TIMELINE_GAP"}
    assert {error.code for error in validate(overlap).errors} == {
        "SCENE_TIMELINE_OVERLAP"
    }
    assert {error.code for error in validate(wrong_duration).errors} == {
        "SCRIPT_DURATION_MISMATCH"
    }
