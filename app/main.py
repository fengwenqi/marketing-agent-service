from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.campaigns.router import router as campaigns_router
from app.database import SessionFactory
from app.hermes.gateway_adapter import HermesGatewayAdapter
from app.hermes.ports import HermesPort
from app.media_plans.router import router as media_plans_router
from app.settings import get_settings
from app.strategies.router import router as strategies_router
from app.video_scripts.executor import VideoScriptBatchExecutor
from app.video_scripts.router import router as video_scripts_router


def create_app(*, hermes: HermesPort | None = None) -> FastAPI:
    """应用工厂让测试可以创建全新的 FastAPI 实例，避免全局状态相互污染。"""

    settings = get_settings()
    if hermes is None:
        hermes = HermesGatewayAdapter(
            hermes_source=settings.hermes_workspace / "hermes-agent",
            workspace=settings.hermes_workspace,
            turn_timeout_seconds=settings.hermes_turn_timeout_seconds,
        )
    executor = VideoScriptBatchExecutor(
        SessionFactory,
        hermes,
        settings.video_script_max_concurrency,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # 进程异常退出时 RUNNING 作业不会丢失；重启后转为可重试状态。
        await executor.recover_interrupted()
        yield
        # 正常关闭会等待已接收的后台批次结束，避免生成结果写到一半。
        await executor.shutdown()

    app = FastAPI(
        title="恒智影营销云 Marketing Agent Service",
        version="0.1.0",
        lifespan=lifespan,
    )
    # Hermes 客户端配置属于进程级依赖；数据库 Session 则由每个 HTTP 请求单独创建。
    app.state.hermes = hermes
    app.state.video_script_executor = executor
    app.include_router(campaigns_router)
    app.include_router(strategies_router)
    app.include_router(media_plans_router)
    app.include_router(video_scripts_router)

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
