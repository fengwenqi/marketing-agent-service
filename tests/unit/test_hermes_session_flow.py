from pathlib import Path
from typing import Any

import pytest

from app.hermes.gateway_adapter import HermesGatewayAdapter
from app.hermes.ports import ContextIntakeRequest


class MissingSessionError(ValueError):
    pass


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


def request(*, stored_session_id: str | None = None) -> ContextIntakeRequest:
    return ContextIntakeRequest(
        campaign_id="campaign-1",
        request_id="request-1",
        message="继续补充目标人群",
        stored_session_id=stored_session_id,
        context={"goal": {"product": "小米17 Pro"}},
        recent_messages=[{"role": "assistant", "content": "目标人群是谁？"}],
    )


def adapter() -> HermesGatewayAdapter:
    return HermesGatewayAdapter(Path("hermes-source"), Path("workspace"))


@pytest.mark.asyncio
async def test_first_turn_creates_a_stored_session() -> None:
    client = FakeGatewayClient(
        {
            "runtime.describe": {"session_create": {"parameters": ["skills"]}},
            "session.create": {
                "session_id": "runtime-1",
                "stored_session_id": "stored-1",
            },
        }
    )

    runtime_id, stored_id, recovering = await adapter()._open_session(client, request())

    assert (runtime_id, stored_id, recovering) == ("runtime-1", "stored-1", False)
    assert client.calls[-1][0] == "session.create"


@pytest.mark.asyncio
async def test_later_turn_resumes_the_stored_session() -> None:
    client = FakeGatewayClient(
        {
            "session.resume": {
                "session_id": "runtime-2",
                "stored_session_id": "stored-1",
            }
        }
    )

    runtime_id, stored_id, recovering = await adapter()._open_session(
        client, request(stored_session_id="stored-1")
    )

    assert (runtime_id, stored_id, recovering) == ("runtime-2", "stored-1", False)
    assert client.calls == [("session.resume", {"session_id": "stored-1", "source": "tool"})]


@pytest.mark.asyncio
async def test_missing_stored_session_creates_replacement_for_hydration() -> None:
    client = FakeGatewayClient(
        {
            "session.resume": MissingSessionError("not_found"),
            "runtime.describe": {"session_create": {"parameters": ["skills"]}},
            "session.create": {
                "session_id": "runtime-new",
                "stored_session_id": "stored-new",
            },
        }
    )

    runtime_id, stored_id, recovering = await adapter()._open_session(
        client, request(stored_session_id="stored-missing")
    )

    assert (runtime_id, stored_id, recovering) == ("runtime-new", "stored-new", True)


def test_recovery_prompt_contains_postgres_context_and_recent_history() -> None:
    prompt = HermesGatewayAdapter._prompt(request(stored_session_id="missing"), recovering=True)

    assert "小米17 Pro" in prompt
    assert "目标人群是谁" in prompt
    assert "继续补充目标人群" in prompt
