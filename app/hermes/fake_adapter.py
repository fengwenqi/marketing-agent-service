from collections import deque

from app.hermes.contracts import (
    MarketingContextResult,
    MarketingStrategyResult,
    StageMediaPlanResult,
)
from app.hermes.ports import (
    ContextIntakeRequest,
    ContextIntakeResponse,
    MediaPlanGenerationRequest,
    MediaPlanGenerationResponse,
    StrategyGenerationRequest,
    StrategyGenerationResponse,
)


class FakeHermesAdapter:
    """测试替身只返回预设业务结果，不模拟 Gateway 的内部实现。"""

    def __init__(
        self,
        results: list[MarketingContextResult | ContextIntakeResponse | Exception],
        strategy_results: list[
            MarketingStrategyResult | StrategyGenerationResponse | Exception
        ]
        | None = None,
        media_plan_results: list[
            StageMediaPlanResult | MediaPlanGenerationResponse | Exception
        ]
        | None = None,
    ) -> None:
        self._results = deque(results)
        self.call_count = 0
        self.requests: list[ContextIntakeRequest] = []
        self._strategy_results = deque(strategy_results or [])
        self.strategy_call_count = 0
        self.strategy_requests: list[StrategyGenerationRequest] = []
        self._media_plan_results = deque(media_plan_results or [])
        self.media_plan_call_count = 0
        self.media_plan_requests: list[MediaPlanGenerationRequest] = []

    async def run_context_intake(self, request: ContextIntakeRequest) -> ContextIntakeResponse:
        self.call_count += 1
        self.requests.append(request)
        result = self._results.popleft()
        if isinstance(result, Exception):
            raise result
        if isinstance(result, ContextIntakeResponse):
            return result
        return ContextIntakeResponse(
            result=result,
            stored_session_id=request.stored_session_id or f"fake-stored-{self.call_count}",
        )

    async def run_strategy_generation(
        self, request: StrategyGenerationRequest
    ) -> StrategyGenerationResponse:
        self.strategy_call_count += 1
        self.strategy_requests.append(request)
        result = self._strategy_results.popleft()
        if isinstance(result, Exception):
            raise result
        if isinstance(result, StrategyGenerationResponse):
            return result
        return StrategyGenerationResponse(
            result=result,
            stored_session_id=request.stored_session_id
            or f"fake-strategy-stored-{self.strategy_call_count}",
        )

    async def run_media_plan_generation(
        self, request: MediaPlanGenerationRequest
    ) -> MediaPlanGenerationResponse:
        self.media_plan_call_count += 1
        self.media_plan_requests.append(request)
        result = self._media_plan_results.popleft()
        if isinstance(result, Exception):
            raise result
        if isinstance(result, MediaPlanGenerationResponse):
            return result
        return MediaPlanGenerationResponse(
            result=result,
            stored_session_id=request.stored_session_id
            or f"fake-media-plan-stored-{self.media_plan_call_count}",
        )

