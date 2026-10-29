from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.settings import get_settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    # 每个 HTTP 请求独占一个 Session；退出依赖时统一释放连接。
    async with SessionFactory() as session:
        yield session


# 导入模型是为了让 Alembic 和 metadata 能发现所有表。
from app.models import business as _business  # noqa: E402,F401

