from dataclasses import dataclass
from typing import Protocol

from app.hermes.contracts import MarketingContextResult


@dataclass(frozen=True)
class ContextIntakeRequest:
    campaign_id: str
    request_id: str
    message: str


class HermesPort(Protocol):
    async def run_context_intake(self, request: ContextIntakeRequest) -> MarketingContextResult: ...

