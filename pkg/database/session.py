from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from pkg.toolkit.json import JsonInputType, orjson_dumps, orjson_loads

SessionProvider = Callable[..., AbstractAsyncContextManager[AsyncSession]]


def new_async_engine(
    *,
    database_uri: str,
    echo: bool = True,
    pool_pre_ping: bool = True,
    pool_size: int = 10,
    max_overflow: int = 20,
    pool_timeout: int = 30,
    pool_recycle: int = 1800,
    json_serializer: Callable[[Any], str] = orjson_dumps,
    json_deserializer: Callable[[JsonInputType], Any] = orjson_loads,
) -> AsyncEngine:
    """按项目默认连接池与 JSON 编解码配置创建异步 engine。"""
    return create_async_engine(
        url=database_uri,
        echo=echo,
        pool_pre_ping=pool_pre_ping,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_recycle=pool_recycle,
        json_serializer=json_serializer,
        json_deserializer=json_deserializer,
    )


def new_async_session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """创建由调用方管理事务与生命周期的异步 session maker。"""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=True,
    )
