from app.campaigns.domain import CampaignEvent
from app.hermes.contracts import MarketingContextResult


def map_context_result(result: MarketingContextResult) -> CampaignEvent:
    """LLM 只报告业务事实，最终状态转换仍由后端代码决定。"""

    if result.status == "READY":
        return CampaignEvent.CONTEXT_READY
    return CampaignEvent.CONTEXT_BLOCKED

