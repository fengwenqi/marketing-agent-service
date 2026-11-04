from dataclasses import dataclass
from typing import Any, Protocol

from app.hermes.contracts import MarketingContextResult


class HermesOutputValidationError(RuntimeError):
    """Hermes 已完成执行，但返回内容不符合 FastAPI 业务契约。"""

    pass


class HermesRequestPreviouslyFailedError(RuntimeError):
    """同一个 Hermes input_id 已经终态失败，不能等待它再次产生完成事件。"""

    pass


class HermesRequestConflictError(RuntimeError):
    """Gateway 拒绝复用参数不一致的持久化请求 ID。"""

    pass


class HermesTurnTimeoutError(TimeoutError):
    """Gateway 连接正常，但 Agent 没有在业务允许的时间内完成本轮任务。"""

    pass


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

