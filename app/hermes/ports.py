from dataclasses import dataclass
from typing import Any, Protocol

from app.hermes.contracts import (
    MarketingContextResult,
    MarketingStrategyResult,
    StageMediaPlanResult,
    VideoScriptResult,
)


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


@dataclass(frozen=True)
class StrategyGenerationRequest:
    campaign_id: str
    request_id: str
    marketing_context: dict[str, Any]
    stored_session_id: str | None = None
    current_strategy: dict[str, Any] | None = None
    revision_instruction: str | None = None


@dataclass(frozen=True)
class StrategyGenerationResponse:
    result: MarketingStrategyResult
    stored_session_id: str


@dataclass(frozen=True)
class MediaPlanGenerationRequest:
    campaign_id: str
    stage_code: str
    request_id: str
    marketing_context: dict[str, Any]
    confirmed_strategy: dict[str, Any]
    confirmation_inputs: dict[str, Any]
    stored_session_id: str | None = None
    current_plan: dict[str, Any] | None = None
    revision_instruction: str | None = None


@dataclass(frozen=True)
class MediaPlanGenerationResponse:
    result: StageMediaPlanResult
    stored_session_id: str


@dataclass(frozen=True)
class VideoScriptGenerationRequest:
    campaign_id: str
    stage_code: str
    request_id: str
    marketing_context: dict[str, Any]
    confirmed_strategy: dict[str, Any]
    confirmed_media_plan: dict[str, Any]
    creative_slot: dict[str, Any]
    stored_session_id: str | None = None
    current_script: dict[str, Any] | None = None
    revision_instruction: str | None = None


@dataclass(frozen=True)
class VideoScriptGenerationResponse:
    result: VideoScriptResult
    stored_session_id: str


class HermesPort(Protocol):
    async def run_context_intake(self, request: ContextIntakeRequest) -> ContextIntakeResponse: ...

    async def run_strategy_generation(
        self, request: StrategyGenerationRequest
    ) -> StrategyGenerationResponse: ...

    async def run_media_plan_generation(
        self, request: MediaPlanGenerationRequest
    ) -> MediaPlanGenerationResponse: ...

    async def run_video_script_generation(
        self, request: VideoScriptGenerationRequest
    ) -> VideoScriptGenerationResponse: ...
