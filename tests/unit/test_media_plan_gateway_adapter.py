import json
from pathlib import Path
from typing import Any

import pytest

from app.hermes.gateway_adapter import HermesGatewayAdapter
from app.hermes.ports import HermesOutputValidationError, MediaPlanGenerationRequest
from tests.unit.test_media_plan_contract import valid_media_plan_payload
from tests.unit.test_strategy_contract import valid_strategy_payload


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


def request(*, stored_session_id: str | None = None) -> MediaPlanGenerationRequest:
    return MediaPlanGenerationRequest(
        campaign_id="campaign-1",
        stage_code="A",
        request_id="media-plan-request-1",
        marketing_context={"goal": {"product": "小米17 Pro"}},
        confirmed_strategy=valid_strategy_payload(),
        confirmation_inputs={"campaign_total_budget": "10000.00"},
        stored_session_id=stored_session_id,
        current_plan=valid_media_plan_payload() if stored_session_id else None,
        revision_instruction="强化核心人群测试" if stored_session_id else None,
    )


def adapter() -> HermesGatewayAdapter:
    return HermesGatewayAdapter(Path("hermes-source"), Path("workspace"))


@pytest.mark.asyncio
async def test_first_media_plan_generation_creates_skill_only_session() -> None:
    client = FakeGatewayClient(
        {
            "runtime.describe": {"session_create": {"parameters": ["skills"]}},
            "session.create": {
                "session_id": "runtime-media-plan",
                "stored_session_id": "stored-media-plan",
            },
        }
    )

    opened = await adapter()._open_media_plan_session(client, request())

    assert opened == ("runtime-media-plan", "stored-media-plan", False)
    assert client.calls[-1] == (
        "session.create",
        {
            "request_id": "media-plan-request-1",
            "source": "tool",
            "cwd": "workspace",
            "skills": ["marketing-media-plan"],
        },
    )


@pytest.mark.asyncio
async def test_media_plan_revision_resumes_stored_session() -> None:
    client = FakeGatewayClient(
        {
            "session.resume": {
                "session_id": "runtime-revision",
                "stored_session_id": "stored-media-plan",
            }
        }
    )

    opened = await adapter()._open_media_plan_session(
        client, request(stored_session_id="stored-media-plan")
    )

    assert opened == ("runtime-revision", "stored-media-plan", False)
    assert client.calls == [
        ("session.resume", {"session_id": "stored-media-plan", "source": "tool"})
    ]


def test_media_plan_prompt_contains_all_business_inputs() -> None:
    prompt = adapter()._media_plan_prompt(
        request(stored_session_id="stored-media-plan")
    )

    assert "小米17 Pro" in prompt
    assert "起量赛马" in prompt
    assert "10000.00" in prompt
    assert "强化核心人群测试" in prompt
    assert "A 阶段起量赛马投放草案" in prompt


def test_media_plan_result_validation_accepts_contract() -> None:
    result = adapter()._validate_media_plan_result(
        json.dumps(valid_media_plan_payload(), ensure_ascii=False)
    )

    assert result.stage_code == "A"
    assert len(result.creative_slots) == 15


def test_media_plan_result_validation_rejects_invalid_output() -> None:
    with pytest.raises(HermesOutputValidationError):
        adapter()._validate_media_plan_result(
            '{"schema_version":"1.0","stage_code":"A"}'
        )
