from pathlib import Path

import pytest

from app.hermes.gateway_adapter import HermesGatewayAdapter, HermesGatewayError


def test_turn_timeout_is_configurable() -> None:
    adapter = HermesGatewayAdapter(Path("hermes-source"), Path("workspace"), 720)

    assert adapter.turn_timeout_seconds == 720


def test_completion_uses_streamed_text_when_completion_is_empty() -> None:
    chunks: list[str] = []

    assert HermesGatewayAdapter._consume_turn_event(
        "message.delta", {"text": '{"status":'}, chunks
    ) is None
    assert HermesGatewayAdapter._consume_turn_event(
        "message.delta", {"text": '"BLOCKED"}'}, chunks
    ) is None

    text = HermesGatewayAdapter._consume_turn_event(
        "message.complete", {"text": "", "status": "complete"}, chunks
    )

    assert text == '{"status":"BLOCKED"}'


def test_completion_prefers_authoritative_completion_text() -> None:
    chunks = ["partial"]

    text = HermesGatewayAdapter._consume_turn_event(
        "message.complete", {"text": "final", "status": "complete"}, chunks
    )

    assert text == "final"


def test_completion_rejects_empty_response() -> None:
    with pytest.raises(HermesGatewayError, match="without response text"):
        HermesGatewayAdapter._consume_turn_event(
            "message.complete", {"text": "", "status": "complete"}, []
        )


def test_completion_surfaces_gateway_turn_error() -> None:
    with pytest.raises(HermesGatewayError, match="provider unavailable"):
        HermesGatewayAdapter._consume_turn_event(
            "message.complete",
            {
                "text": "Error: provider unavailable",
                "status": "error",
                "error": "provider unavailable",
            },
            [],
        )
