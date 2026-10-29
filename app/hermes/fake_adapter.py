from collections import deque

from app.hermes.contracts import MarketingContextResult
from app.hermes.ports import ContextIntakeRequest


class FakeHermesAdapter:
    """测试替身只返回预设业务结果，不模拟 Gateway 的内部实现。"""

    def __init__(self, results: list[MarketingContextResult | Exception]) -> None:
        self._results = deque(results)
        self.call_count = 0

    async def run_context_intake(self, request: ContextIntakeRequest) -> MarketingContextResult:
        _ = request
        self.call_count += 1
        result = self._results.popleft()
        if isinstance(result, Exception):
            raise result
        return result

