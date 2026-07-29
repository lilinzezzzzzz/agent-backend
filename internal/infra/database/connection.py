"""数据库连接管理"""

import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from internal.config import settings
from pkg.database.session import new_async_engine, new_async_session_maker
from pkg.logger import logger
from pkg.toolkit.json import orjson_dumps, orjson_loads

# 全局单例变量，初始为 None
_engine: AsyncEngine | None = None
_session_maker: async_sessionmaker[AsyncSession] | None = None

# 只读副本全局变量
_read_engine: AsyncEngine | None = None
_read_session_maker: async_sessionmaker[AsyncSession] | None = None


# ---------------------- 1. 生命周期管理 ----------------------


def init_async_db(echo: bool | None = None) -> None:
    """
    初始化数据库连接池。
    应在 FastAPI lifespan 或 Celery worker_process_init 中调用。

    Args:
        echo: 是否输出 SQL 日志，None 时使用配置文件中的值
    """
    global _engine, _session_maker, _read_engine, _read_session_maker
    logger.info("Initializing Database Connection...")
    # 幂等性检查：如果已经初始化，直接返回
    if _engine is not None:
        logger.info("Database connection already initialized.")
        return

    # 使用传入的 echo 参数，如果为 None 则使用配置
    db_echo = echo if echo is not None else settings.DB_ECHO

    # 1. 创建主库 Engine
    _engine = new_async_engine(
        database_uri=settings.sqlalchemy_database_uri,
        echo=db_echo,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,
        pool_recycle=1800,
        json_serializer=orjson_dumps,
        json_deserializer=orjson_loads,
    )

    # 2. 注册主库 SQL 监控事件
    _register_event_listeners(_engine)

    # 3. 创建主库 SessionMaker
    _session_maker = new_async_session_maker(engine=_engine)
    logger.success("Database connection initialized successfully.")

    # 4. 初始化只读副本（如果配置了）
    read_uri = settings.sqlalchemy_read_database_uri
    if read_uri is not None:
        logger.info("Initializing Read Replica Database Connection...")
        _read_engine = new_async_engine(
            database_uri=read_uri,
            echo=db_echo,
            pool_pre_ping=True,
            pool_size=20,  # 读库通常承载更多查询，连接池更大
            max_overflow=30,
            pool_timeout=30,
            pool_recycle=1800,
            json_serializer=orjson_dumps,
            json_deserializer=orjson_loads,
        )
        _register_event_listeners(_read_engine)
        _read_session_maker = new_async_session_maker(engine=_read_engine)
        logger.success("Read Replica Database connection initialized successfully.")


async def close_async_db() -> None:
    """关闭数据库连接池（包括主库和只读副本）"""
    global _engine, _session_maker, _read_engine, _read_session_maker
    if _read_engine:
        await _read_engine.dispose()
        logger.warning("Read Replica Database connection disposed.")
    _read_engine = None
    _read_session_maker = None

    if _engine:
        await _engine.dispose()
        logger.warning("Database connection disposed.")
    _engine = None
    _session_maker = None


def reset_async_db() -> None:
    """
    重置数据库连接池（同步版本，包括主库和只读副本）。
    用于 Celery 任务中使用 asyncio.run/anyio.run 创建新事件循环前，
    先清理旧的连接池，避免事件循环绑定冲突。

    注意：此函数不会异步关闭连接，仅重置全局变量。
    """
    global _engine, _session_maker, _read_engine, _read_session_maker
    _engine = None
    _session_maker = None
    _read_engine = None
    _read_session_maker = None


# ---------------------- 2. Session 获取 ----------------------


@asynccontextmanager
async def get_session(autoflush: bool = True) -> AsyncGenerator[AsyncSession, Any]:
    """
    通用的 Session 获取上下文管理器（主库），FastAPI 和 Celery 均可用。
    """
    session_maker = _session_maker

    if session_maker is None:
        raise RuntimeError("Database is not initialized. Call init_db() first.")

    async with session_maker() as session:
        if autoflush:
            try:
                yield session
            except Exception:
                if session.is_active:
                    await session.rollback()
                raise
        else:
            with session.no_autoflush:
                try:
                    yield session
                except Exception:
                    if session.is_active:
                        await session.rollback()
                    raise


@asynccontextmanager
async def get_read_session(autoflush: bool = True) -> AsyncGenerator[AsyncSession, Any]:
    """
    只读副本 Session 获取上下文管理器。
    如果未配置只读副本，自动 fallback 到主库（优雅降级）。
    """
    # 优雅降级：未配置读库时使用主库
    session_maker = _read_session_maker if _read_session_maker is not None else _session_maker

    if session_maker is None:
        raise RuntimeError("Database is not initialized. Call init_db() first.")

    async with session_maker() as session:
        if autoflush:
            try:
                yield session
            except Exception:
                if session.is_active:
                    await session.rollback()
                raise
        else:
            with session.no_autoflush:
                try:
                    yield session
                except Exception:
                    if session.is_active:
                        await session.rollback()
                    raise


# ---------------------- 3. SQL 监控逻辑 (私有) ----------------------


def _register_event_listeners(engine: AsyncEngine):
    """注册 SQLAlchemy 事件监听"""
    event.listen(engine.sync_engine, "before_cursor_execute", _before_cursor_execute)
    event.listen(engine.sync_engine, "after_cursor_execute", _after_cursor_execute)


def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    if context:
        context.query_start_time = time.perf_counter()


def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    if not context or not hasattr(context, "query_start_time"):
        return

    elapsed = time.perf_counter() - context.query_start_time

    # 获取配置 (带有默认值防止报错)
    slow_threshold = getattr(settings, "SLOW_SQL_THRESHOLD", 0.5)
    is_debug = getattr(settings, "DEBUG", False)

    if elapsed > slow_threshold:
        sql_str = _get_formatted_sql(context, statement, parameters)
        logger.warning(f"SLOW SQL ({elapsed:.4f}s): {sql_str}")
    elif is_debug:
        sql_str = _get_formatted_sql(context, statement, parameters)
        logger.info(f"SQL ({elapsed:.4f}s): {sql_str}")


def _get_formatted_sql(context, statement, parameters) -> str:
    try:
        if context and context.compiled:
            return context.compiled.statement.compile(dialect=context.dialect, compile_kwargs={"literal_binds": True})
        if parameters:
            return f"{statement} | Params: {parameters}"
        return str(statement)
    except Exception as e:
        return f"SQL_FORMAT_ERROR: {e} | Raw: {statement}"
