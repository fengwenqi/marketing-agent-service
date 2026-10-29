from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """集中管理运行配置，避免业务代码到处读取环境变量。"""

    app_env: str = "development"
    database_url: str = (
        "postgresql+asyncpg://marketing_agent:marketing_agent_dev@127.0.0.1:5432/marketing_agent"
    )
    hermes_profile: str = "marketing-agent"
    hermes_workspace: Path = Path(r"D:\my-project\AI\Hermes")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    # Settings 在进程生命周期内不会频繁改变，缓存可确保所有模块看到同一份配置。
    return Settings()

