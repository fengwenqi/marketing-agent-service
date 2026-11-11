import json
from pathlib import Path
from typing import Any

import pytest

from app.hermes.contracts import VideoScriptResult
from app.hermes.fake_adapter import FakeHermesAdapter
from app.hermes.gateway_adapter import HermesGatewayAdapter
from app.hermes.ports import (
    HermesOutputValidationError,
    VideoScriptGenerationRequest,
)
from tests.unit.test_media_plan_contract import valid_media_plan_payload
from tests.unit.test_strategy_contract import valid_strategy_payload
from tests.unit.test_video_script_contract import valid_video_script_payload


class FakeGatewayClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def rpc(self, method: str, **params: Any) -> dict[str, Any]:
        self.calls.append((method, params))
        response = self.responses[method]
        if isinstance(response, Exception):
            raise response
        return response


def request(*, stored_session_id: str | None = None) -> VideoScriptGenerationRequest:
    return VideoScriptGenerationRequest(
        campaign_id="campaign-1",
        stage_code="A",
        request_id="video-script-oral-1",
        marketing_context={"goal": {"product": "小米17 Pro"}},
        confirmed_strategy=valid_strategy_payload(),
        confirmed_media_plan=valid_media_plan_payload(),
        creative_slot={
            "id": "oral-1",
            "format": "oral_video",
            "direction": "影像卖点",
            "test_variable": "opening",
            "priority": 1,
        },
        stored_session_id=stored_session_id,
        current_script=valid_video_script_payload() if stored_session_id else None,
        revision_instruction="强化前三秒钩子" if stored_session_id else None,
    )


def adapter() -> HermesGatewayAdapter:
    return HermesGatewayAdapter(Path("hermes-source"), Path("workspace"))


@pytest.mark.asyncio
async def test_first_video_script_generation_creates_skill_only_session() -> None:
    client = FakeGatewayClient(
        {
            "runtime.describe": {"session_create": {"parameters": ["skills"]}},
            "session.create": {
                "session_id": "runtime-video-script",
                "stored_session_id": "stored-video-script",
            },
        }
    )

    opened = await adapter()._open_video_script_session(client, request())

    assert opened == ("runtime-video-script", "stored-video-script", False)
    assert client.calls[-1] == (
        "session.create",
        {
            "request_id": "video-script-oral-1",
            "source": "tool",
            "cwd": "workspace",
            "skills": ["marketing-video-script"],
        },
    )


@pytest.mark.asyncio
async def test_video_script_revision_resumes_stored_session() -> None:
    client = FakeGatewayClient(
        {
            "session.resume": {
                "session_id": "runtime-revision",
                "stored_session_id": "stored-video-script",
            }
        }
    )

    opened = await adapter()._open_video_script_session(
        client, request(stored_session_id="stored-video-script")
    )

    assert opened == ("runtime-revision", "stored-video-script", False)
    assert client.calls == [
        ("session.resume", {"session_id": "stored-video-script", "source": "tool"})
    ]


def test_video_script_prompt_contains_fixed_sources_and_revision() -> None:
    prompt = adapter()._video_script_prompt(request(stored_session_id="stored-video-script"))

    assert "小米17 Pro" in prompt
    assert "起量赛马" in prompt
    assert "A 阶段起量赛马投放草案" in prompt
    assert "oral-1" in prompt
    assert "强化前三秒钩子" in prompt
    assert "手机也能拍出电影感吗" in prompt


def test_video_script_result_validation_accepts_contract() -> None:
    result = adapter()._validate_video_script_result(
        json.dumps(valid_video_script_payload(), ensure_ascii=False)
    )

    assert result.creative_slot_id == "oral-1"


def test_video_script_result_validation_rejects_invalid_output() -> None:
    with pytest.raises(HermesOutputValidationError):
        adapter()._validate_video_script_result('{"schema_version":"1.0"}')


@pytest.mark.asyncio
async def test_fake_adapter_records_video_script_requests_and_errors() -> None:
    hermes = FakeHermesAdapter(
        [],
        video_script_results=[
            VideoScriptResult.model_validate(valid_video_script_payload()),
            RuntimeError("down"),
        ],
    )

    response = await hermes.run_video_script_generation(request())
    with pytest.raises(RuntimeError, match="down"):
        await hermes.run_video_script_generation(request())

    assert response.result.creative_slot_id == "oral-1"
    assert response.stored_session_id == "fake-video-script-stored-1"
    assert hermes.video_script_call_count == 2
    assert len(hermes.video_script_requests) == 2
