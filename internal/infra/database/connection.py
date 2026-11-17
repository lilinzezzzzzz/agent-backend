"""数据库连接管理"""

import builtins
import time
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from internal import BASE_DIR
from internal.config import settings
from internal.infra.lifecycle import close_resources
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
    应在 FastAPI lifespan 或 Celery 任务级事件循环中调用。

    Args:
        echo: 是否输出 SQL 日志，None 时使用配置文件中的值
    """
    global _engine, _session_maker, _read_engine, _read_session_maker
    logger.info("Initializing Database Connection...")
    if _engine is not None or _read_engine is not None:
        if _session_maker is None or (
            _read_engine is not None and _read_session_maker is None
        ):
            raise RuntimeError(
                "Await close_async_db() before reinitializing database resources."
            )
        logger.info("Database connection already initialized.")
        return

    # 使用传入的 echo 参数，如果为 None 则使用配置
    db_echo = echo if echo is not None else settings.db.echo

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
        sqlite_busy_timeout_ms=settings.db.sqlite.busy_timeout_ms,
        sqlite_allow_create=settings.db.sqlite.allow_create,
        sqlite_explicit_transactions=settings.db.sqlite.explicit_transactions,
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


async def check_database_revision() -> None:
    """在运行时资源所属 loop 中校验 PostgreSQL 主库版本，不执行迁移。"""
    if _engine is None:
        raise RuntimeError("Database is not initialized. Call init_async_db() first.")
    if _engine.dialect.name != "postgresql":
        return
    connection = await _engine.connect()
    cleanup = AsyncExitStack()
    cleanup.push_async_callback(connection.close)
    try:
        await connection.run_sync(_check_database_revision)
    finally:
        await close_resources(cleanup)


def _check_database_revision(connection: Connection) -> None:
    expected = set(ScriptDirectory(str(BASE_DIR / "migrations")).get_heads())
    if len(expected) != 1:
        raise RuntimeError("Migration history must contain exactly one head.")
    current = set(MigrationContext.configure(connection).get_current_heads())
    if current != expected:
        raise RuntimeError(
            "Database schema revision is not current. "
            "Verify the target database and run 'uv run alembic upgrade head' "
            "with the same APP_ENV and configuration before starting."
        )


async def close_async_db() -> None:
    """由生命周期所有者调用；两个池分别清理，失败池保留引用供顺序重试。"""
    global _engine, _session_maker, _read_engine, _read_session_maker
    # 所有者应先结束业务工作和 session；收尾开始后不再发放新的 session。
    _session_maker = None
    _read_session_maker = None
    errors: list[BaseException] = []
    if _read_engine is not None:
        try:
            await _read_engine.dispose()
        except BaseException as exc:
            errors.append(exc)
        else:
            _read_engine = None
    if _engine is not None:
        try:
            await _engine.dispose()
        except BaseException as exc:
            errors.append(exc)
        else:
            _engine = None
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise builtins.BaseExceptionGroup("Database connection cleanup failed", errors)
    logger.warning("Database connections disposed.")


def reset_async_db() -> None:
    """仅清除无活动 engine 的引用；同步 reset 不能替代异步 dispose。"""
    global _session_maker, _read_session_maker
    if _engine is not None or _read_engine is not None:
        raise RuntimeError(
            "Await close_async_db() before resetting database resources."
        )
    _session_maker = None
    _read_session_maker = None


# ---------------------- 2. Session 获取 ----------------------


@asynccontextmanager
async def get_session(autoflush: bool = True) -> AsyncGenerator[AsyncSession, None]:
    """获取主库 session，事务由调用方控制，退出时等待 session 关闭。"""
    async with _session_scope(_session_maker, autoflush) as session:
        yield session


@asynccontextmanager
async def get_read_session(
    autoflush: bool = True,
) -> AsyncGenerator[AsyncSession, None]:
    """获取只读池 session，未配置副本时使用主库。"""
    maker = _read_session_maker if _read_session_maker is not None else _session_maker
    async with _session_scope(maker, autoflush) as session:
        yield session


@asynccontextmanager
async def _session_scope(
    maker: async_sessionmaker[AsyncSession] | None,
    autoflush: bool,
) -> AsyncGenerator[AsyncSession, None]:
    if maker is None:
        raise RuntimeError("Database is not initialized. Call init_async_db() first.")
    session = maker()
    cleanup = AsyncExitStack()
    cleanup.push_async_callback(session.close)
    try:
        if autoflush:
            yield session
        else:
            with session.no_autoflush:
                yield session
    except Exception:
        if session.is_active:
            await session.rollback()
        raise
    finally:
        # 复用已有取消保护；不让 session 清理在后台晚于 engine.dispose 完成。
        await close_resources(cleanup)


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
    is_debug = settings.debug

    if elapsed > slow_threshold:
        logger.warning(f"SLOW SQL ({elapsed:.4f}s): {statement}")
    elif is_debug:
        # 只记录驱动收到的语句模板，不展开绑定参数或序列化业务 payload。
        logger.info(f"SQL ({elapsed:.4f}s): {statement}")
