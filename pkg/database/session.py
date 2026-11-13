import sqlite3
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any
from urllib.parse import quote

from sqlalchemy import Connection, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.util import asbool

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
    sqlite_busy_timeout_ms: int = 5000,
    sqlite_allow_create: bool = True,
    sqlite_explicit_transactions: bool = False,
) -> AsyncEngine:
    """创建异步 engine，隐藏绑定参数并按需为 SQLite 显式管理 BEGIN。"""
    url = make_url(database_uri)
    backend = url.get_backend_name()
    if backend not in {"postgresql", "sqlite"}:
        raise ValueError(f"Database supports PostgreSQL and SQLite only, got {backend}")
    if backend == "sqlite":
        if (
            type(sqlite_busy_timeout_ms) is not int
            or not 0 < sqlite_busy_timeout_ms <= 2_147_483_647
        ):
            raise ValueError("SQLite busy timeout must be between 1 and 2147483647 ms")
        if "uri" in url.query:
            uri_enabled = asbool(url.query["uri"])
            url = url.update_query_dict({"uri": "true" if uri_enabled else "false"})
        database = url.database
        if (
            not sqlite_allow_create
            and database is not None
            and database not in {"", ":memory:"}
        ):
            # file: 前缀决定 URI 路径，uri 参数只控制驱动是否按 URI 解释。
            if not database.startswith("file:"):
                url = url.set(database=f"file:{quote(database, safe='/')}")
            uri_options = {"uri": "true"}
            if database != "file::memory:" and url.query.get("mode") != "memory":
                # mode=rw 在每次连接时拒绝误建空库，同时保留显式只读要求。
                uri_options["mode"] = "ro" if url.query.get("mode") == "ro" else "rw"
            url = url.update_query_dict(uri_options)
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
    engine = create_async_engine(
        url=url,
        echo=echo,
        hide_parameters=True,
        connect_args={"autocommit": sqlite3.LEGACY_TRANSACTION_CONTROL}
        if backend == "sqlite"
        else {},
        pool_pre_ping=pool_pre_ping,
        json_serializer=json_serializer,
        json_deserializer=json_deserializer,
        **pool_options,
    )
    if backend == "sqlite":

        @event.listens_for(engine.sync_engine, "connect")
        def configure_sqlite_connection(connection: Any, _record: Any) -> None:
            if sqlite_explicit_transactions:
                # 禁用驱动的 legacy BEGIN，由 SQLAlchemy 统一管理读、写和 DDL 事务。
                connection.isolation_level = None
            cursor = connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute(f"PRAGMA busy_timeout={sqlite_busy_timeout_ms}")
            finally:
                cursor.close()

        if sqlite_explicit_transactions:

            @event.listens_for(engine.sync_engine, "begin")
            def begin_sqlite_transaction(connection: Connection) -> None:
                connection.exec_driver_sql("BEGIN")

    return engine


def new_async_session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """创建由调用方管理事务与生命周期的异步 session maker。"""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=True,
    )
