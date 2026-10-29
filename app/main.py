from fastapi import FastAPI

from app.campaigns.router import router as campaigns_router
from app.campaigns.service import CampaignService
from app.hermes.gateway_adapter import HermesGatewayAdapter
from app.hermes.ports import HermesPort
from app.settings import get_settings


def create_app(*, hermes: HermesPort | None = None) -> FastAPI:
    """应用工厂让测试可以创建全新的 FastAPI 实例，避免全局状态相互污染。"""

    app = FastAPI(
        title="恒智影营销云 Marketing Agent Service",
        version="0.1.0",
    )
    if hermes is None:
        settings = get_settings()
        hermes = HermesGatewayAdapter(
            hermes_source=settings.hermes_workspace / "hermes-agent",
            workspace=settings.hermes_workspace,
        )
    app.state.campaign_service = CampaignService(hermes)
    app.include_router(campaigns_router)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        # 健康检查故意不访问数据库或 Hermes：它只表示 HTTP 进程已经能够响应。
        return {
            "name": "marketing-agent-service",
            "status": "ok",
            "version": "0.1.0",
        }

    return app


app = create_app()

