from dataclasses import dataclass
from typing import Any, Protocol

from app.hermes.contracts import MarketingContextResult


@dataclass(frozen=True)
class ContextIntakeRequest:
    campaign_id: str
    request_id: str
    message: str
    stored_session_id: str | None = None
    context: dict[str, Any] | None = None
    recent_messages: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class ContextIntakeResponse:
    result: MarketingContextResult
    stored_session_id: str


class HermesPort(Protocol):
    async def run_context_intake(self, request: ContextIntakeRequest) -> ContextIntakeResponse: ...

