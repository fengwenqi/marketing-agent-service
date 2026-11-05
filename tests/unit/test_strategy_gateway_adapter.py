import json
from pathlib import Path
from typing import Any

import pytest

from app.hermes.gateway_adapter import HermesGatewayAdapter
from app.hermes.ports import (
    HermesOutputValidationError,
    StrategyGenerationRequest,
)
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


def request(*, stored_session_id: str | None = None) -> StrategyGenerationRequest:
    return StrategyGenerationRequest(
        campaign_id="campaign-1",
        request_id="strategy-request-1",
        marketing_context={"goal": {"product": "小米17 Pro"}},
        stored_session_id=stored_session_id,
        current_strategy=valid_strategy_payload() if stored_session_id else None,
        revision_instruction="B阶段预算提高" if stored_session_id else None,
    )


def adapter() -> HermesGatewayAdapter:
    return HermesGatewayAdapter(Path("hermes-source"), Path("workspace"))


@pytest.mark.asyncio
async def test_first_strategy_generation_creates_strategy_only_session() -> None:
    client = FakeGatewayClient(
        {
            "runtime.describe": {"session_create": {"parameters": ["skills"]}},
            "session.create": {
                "session_id": "runtime-strategy",
                "stored_session_id": "stored-strategy",
            },
        }
    )

    opened = await adapter()._open_strategy_session(client, request())

    assert opened == ("runtime-strategy", "stored-strategy", False)
    assert client.calls[-1] == (
        "session.create",
        {
            "request_id": "strategy-request-1",
            "source": "tool",
            "cwd": "workspace",
            "skills": ["marketing-strategy"],
        },
    )


@pytest.mark.asyncio
async def test_strategy_revision_resumes_strategy_session() -> None:
    client = FakeGatewayClient(
        {
            "session.resume": {
                "session_id": "runtime-revision",
                "stored_session_id": "stored-strategy",
            }
        }
    )

    opened = await adapter()._open_strategy_session(
        client, request(stored_session_id="stored-strategy")
    )

    assert opened == ("runtime-revision", "stored-strategy", False)
    assert client.calls == [
        ("session.resume", {"session_id": "stored-strategy", "source": "tool"})
    ]


def test_strategy_revision_prompt_contains_context_current_version_and_instruction() -> None:
    prompt = adapter()._strategy_prompt(request(stored_session_id="stored-strategy"))

    assert "小米17 Pro" in prompt
    assert "B阶段预算提高" in prompt
    assert "起量赛马" in prompt


def test_strategy_result_validation_accepts_contract() -> None:
    result = adapter()._validate_strategy_result(
        json.dumps(valid_strategy_payload(), ensure_ascii=False)
    )

    assert [stage.code for stage in result.stages] == ["A", "B", "C"]


def test_strategy_result_validation_rejects_invalid_output() -> None:
    with pytest.raises(HermesOutputValidationError):
        adapter()._validate_strategy_result('{"schema_version":"1.0","stages":[]}')
