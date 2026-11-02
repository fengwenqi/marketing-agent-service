from collections import deque

from app.hermes.contracts import MarketingContextResult
from app.hermes.ports import ContextIntakeRequest, ContextIntakeResponse


class FakeHermesAdapter:
    """测试替身只返回预设业务结果，不模拟 Gateway 的内部实现。"""

    def __init__(
        self,
        results: list[MarketingContextResult | ContextIntakeResponse | Exception],
    ) -> None:
        self._results = deque(results)
        self.call_count = 0
        self.requests: list[ContextIntakeRequest] = []

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

