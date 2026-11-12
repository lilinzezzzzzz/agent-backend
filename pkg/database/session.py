from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from sqlalchemy.engine import URL, make_url
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
    database_uri: str | URL,
    echo: bool = True,
    pool_pre_ping: bool = True,
    pool_size: int = 10,
    max_overflow: int = 20,
    pool_timeout: int = 30,
    pool_recycle: int = 1800,
    json_serializer: Callable[[Any], str] = orjson_dumps,
    json_deserializer: Callable[[JsonInputType], Any] = orjson_loads,
) -> AsyncEngine:
    """创建 PostgreSQL / SQLite 异步 engine，SQLite 使用方言默认连接池。"""
    backend = make_url(database_uri).get_backend_name()
    if backend not in {"postgresql", "sqlite"}:
        raise ValueError(f"Database supports PostgreSQL and SQLite only, got {backend}")
    pool_options = (
        {}
        if backend == "sqlite"
        else {
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "pool_timeout": pool_timeout,
            "pool_recycle": pool_recycle,
        }
    )
    return create_async_engine(
        url=database_uri,
        echo=echo,
        pool_pre_ping=pool_pre_ping,
        json_serializer=json_serializer,
        json_deserializer=json_deserializer,
        **pool_options,
    )


def new_async_session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """创建由调用方管理事务与生命周期的异步 session maker。"""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=True,
    )
