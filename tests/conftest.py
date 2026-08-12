"""
Pytest 配置文件 (conftest.py)

提供测试运行所需的共享 fixtures、hooks 和配置。
所有 fixtures 和 hooks 在此文件定义后，可在任意测试文件中直接使用。

主要功能：
1. Mock 外部依赖（logger、UUIDv7、配置等）
2. 提供数据库测试 fixtures（内存 SQLite）
3. 提供 Redis 测试 fixtures
4. 提供 FastAPI 测试客户端
5. 配置 pytest-asyncio 后端
"""

import inspect
import os
import sys
import types
import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from redis.asyncio import ConnectionPool, Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# ==========================================
# 1. 路径配置
# ==========================================

# 获取项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ==========================================
# 2. Mock 外部依赖 (必须在导入内部模块之前)
# ==========================================

_skip_logger_mock = False
_skip_context_mock = False
if sys.argv:
    for arg in sys.argv:
        normalized_arg = arg.replace("\\", "/")
        if (
            "tests/logger" in normalized_arg
            or "logger/" in normalized_arg
            or "test_logger" in normalized_arg
        ):
            _skip_logger_mock = True
        if "test_context" in normalized_arg:
            _skip_context_mock = True

if not _skip_logger_mock:
    # Mock logger
    mock_logger = MagicMock()
    mock_logger.info = MagicMock()
    mock_logger.error = MagicMock()
    mock_logger.warning = MagicMock()
    mock_logger.success = MagicMock()
    mock_logger.debug = MagicMock()
    mock_logger.critical = MagicMock()
    mock_logger.complete = AsyncMock()

    # Mock pkg.logger 模块
    mock_logger_module = types.ModuleType("pkg.logger")
    mock_logger_module.__path__ = [str(PROJECT_ROOT / "pkg" / "logger")]
    mock_logger_module.logger = mock_logger

    sys.modules["pkg.logger"] = mock_logger_module

    from pkg.logger.handler import (  # noqa: PLC0415
        LogFormat,
        LoggerHandler,
        TimezoneType,
    )

    def init_logger(
        *,
        level: str = "INFO",
        base_log_dir: Path | None = None,
        timezone: TimezoneType = "UTC",
        enqueue: bool = True,
        log_format: LogFormat = LogFormat.TEXT,
        write_to_file: bool = False,
        write_to_console: bool = True,
    ):
        logger_manager = LoggerHandler(
            level=level,
            base_log_dir=base_log_dir,
            timezone=timezone,
            enqueue=enqueue,
            log_format=log_format,
        )
        logger_obj = logger_manager.setup(
            write_to_file=write_to_file, write_to_console=write_to_console
        )
        mock_logger_module._logger_manager = logger_manager
        mock_logger_module._logger = logger_obj
        mock_logger_module.logger = logger_obj
        return logger_obj

    def get_logger_manager():
        logger_manager = getattr(mock_logger_module, "_logger_manager", None)
        if logger_manager is None:
            raise RuntimeError(
                "LoggerHandler not initialized. Call init_logger() first."
            )
        return logger_manager

    mock_logger_module.LogFormat = LogFormat
    mock_logger_module.LoggerHandler = LoggerHandler
    mock_logger_module.TimezoneType = TimezoneType
    mock_logger_module.init_logger = init_logger
    mock_logger_module.get_logger_manager = get_logger_manager
    mock_logger_module._logger_manager = None
    mock_logger_module._logger = None
else:
    # logger 测试需要真实的模块，创建一个简单的 mock_logger 供其他 fixture 使用
    mock_logger = MagicMock()
    mock_logger.info = MagicMock()
    mock_logger.error = MagicMock()
    mock_logger.warning = MagicMock()
    mock_logger.success = MagicMock()
    mock_logger.debug = MagicMock()
    mock_logger.critical = MagicMock()
    mock_logger.complete = AsyncMock()

# Mock pkg.request_context 模块
if not _skip_context_mock:

    class MockContextKey(StrEnum):
        USER_ID = "user_id"
        TRACE_ID = "trace_id"

    mock_context = types.ModuleType("pkg.request_context")
    mock_context.ContextKey = MockContextKey
    mock_context.get_user_id = MagicMock(
        return_value=uuid.UUID("00000000-0000-7000-8000-000000000999")
    )
    mock_context.get_trace_id = MagicMock(return_value="test-trace-id")
    mock_context.set_user_id = MagicMock()
    mock_context.set_trace_id = MagicMock()
    mock_context.init = MagicMock(return_value={})
    mock_context.clear = MagicMock()
    mock_context.set_val = MagicMock()
    mock_context.get_val = MagicMock(return_value=None)
    sys.modules["pkg.request_context"] = mock_context
    import pkg  # noqa: PLC0415

    pkg.request_context = mock_context
    logger_handler_module = sys.modules.get("pkg.logger.handler")
    if logger_handler_module is not None:
        logger_handler_module.context = mock_context
    tracing_otel_module = sys.modules.get("pkg.tracing.otel")
    if tracing_otel_module is not None:
        tracing_otel_module.request_context = mock_context

# Mock UUIDv7 生成器
_id_counter = 0


def mock_gen_id() -> uuid.UUID:
    global _id_counter
    _id_counter += 1
    return uuid.UUID(f"00000000-0000-7000-8000-{_id_counter:012x}")


mock_ids_module = types.ModuleType("pkg.ids")
mock_ids_module.uuid7_unique_id = MagicMock(side_effect=mock_gen_id)
mock_ids_module.uuid7_unique_str_id = MagicMock(side_effect=lambda: mock_gen_id().hex)


def _normalize_uuid7_trace_id(value: str | None) -> str | None:
    if not isinstance(value, str) or len(value) != 32:
        return None
    try:
        parsed = uuid.UUID(hex=value)
    except ValueError:
        return None
    return parsed.hex if parsed.version == 7 and parsed.int != 0 else None


mock_ids_module.normalize_uuid7_trace_id = _normalize_uuid7_trace_id
sys.modules["pkg.ids"] = mock_ids_module

# ==========================================
# 3. 测试配置
# ==========================================

# 测试用的内存数据库 URL
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

# 测试用的 Redis URL (使用真实的 Redis 或 fakeredis)
# 注意：集成测试需要真实的 Redis 服务
TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/15")


# ==========================================
# 4. pytest 配置 hooks
# ==========================================


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """
    pytest 配置钩子，在测试开始前执行。

    用于：
    - 注册自定义 markers
    - 设置测试环境
    - 设置 pytest 临时目录
    """
    if config.option.basetemp is None:
        config.option.basetemp = PROJECT_ROOT / ".pytest-tmp"

    # 注册自定义 markers
    config.addinivalue_line(
        "markers", "integration: 集成测试，需要 Redis/Celery 等外部服务"
    )
    config.addinivalue_line("markers", "slow: 慢速测试，耗时较长")
    config.addinivalue_line("markers", "unit: 单元测试，不依赖外部服务")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]):
    """
    修改测试集合的钩子。

    用于：
    - 自动为异步测试添加 asyncio marker
    - 根据文件名自动添加标记
    """
    for item in items:
        # 为所有 async 测试函数自动添加 asyncio marker
        if isinstance(item, pytest.Function) and inspect.iscoroutinefunction(
            item.function
        ):
            item.add_marker(pytest.mark.asyncio)


@pytest.fixture(autouse=True)
def restore_default_module_mocks(request: pytest.FixtureRequest):
    """Restore broad test mocks that individual modules may replace during collection."""
    test_path = str(request.node.fspath).replace("\\", "/")

    if not _skip_context_mock and "test_context" not in test_path:
        sys.modules["pkg.request_context"] = mock_context
        import pkg  # noqa: PLC0415

        pkg.request_context = mock_context
        database_base = sys.modules.get("pkg.database.base")
        if database_base is not None:
            database_base.context = mock_context
        logger_handler_module = sys.modules.get("pkg.logger.handler")
        if logger_handler_module is not None:
            logger_handler_module.context = mock_context
        tracing_otel_module = sys.modules.get("pkg.tracing.otel")
        if tracing_otel_module is not None:
            tracing_otel_module.request_context = mock_context

    restore_logger: tuple[object, object] | None = None
    if _skip_logger_mock and "tests/logger" not in test_path:
        from loguru import logger as real_logger  # noqa: PLC0415

        import pkg.logger as pkg_logger_module  # noqa: PLC0415

        restore_logger = (
            pkg_logger_module._logger,
            pkg_logger_module._logger_manager,
        )
        pkg_logger_module._logger = real_logger

    yield

    if restore_logger is not None:
        import pkg.logger as pkg_logger_module  # noqa: PLC0415

        pkg_logger_module._logger, pkg_logger_module._logger_manager = restore_logger


# ==========================================
# 5. 异步测试配置
# ==========================================


@pytest.fixture
def anyio_backend():
    """配置 anyio 后端为 asyncio"""
    return "asyncio"


# ==========================================
# 6. 数据库 Fixtures
# ==========================================


@pytest_asyncio.fixture(scope="function")
async def db_engine() -> AsyncGenerator[AsyncEngine, None]:
    """
    创建测试用的异步数据库引擎。

    使用内存 SQLite，每个测试函数独立创建和销毁。
    """
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,  # 设为 True 可查看 SQL 语句
        future=True,
    )

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(
    db_engine: AsyncEngine,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """
    创建测试用的数据库会话工厂。

    提供：
    - 自动建表
    - 事务回滚（测试隔离）
    """
    # 导入 Base（需要在 mock 之后导入）
    from pkg.database.base import Base

    # 创建所有表
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 创建会话工厂
    session_maker = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    yield session_maker

    # 清理：删除所有表
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session_with_rollback(
    db_engine: AsyncEngine,
) -> AsyncGenerator[AsyncSession, None]:
    """
    创建带有自动回滚的数据库会话。

    每个测试后自动回滚，确保测试隔离。
    适用于需要真实事务行为的测试。
    """
    from pkg.database.base import Base

    # 创建所有表
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 创建连接并开始事务
    async with db_engine.connect() as connection:
        await connection.begin()

        # 绑定会话到连接
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
        )

        yield session

        # 回滚事务
        await session.close()
        await connection.rollback()

    # 清理
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ==========================================
# 7. Redis Fixtures
# ==========================================


@pytest_asyncio.fixture(scope="function")
async def redis_client() -> AsyncGenerator[Redis, None]:
    """
    创建测试用的 Redis 客户端。

    使用单独的数据库（db=15）避免污染开发数据。
    测试结束后清空该数据库。

    注意：需要运行的 Redis 服务。
    """
    pool = ConnectionPool.from_url(
        TEST_REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
    )
    client = Redis(connection_pool=pool)

    try:
        # 验证连接
        await client.ping()
        yield client
    finally:
        # 清空测试数据库
        await client.flushdb()
        await client.aclose()
        await pool.disconnect()


@pytest_asyncio.fixture(scope="function")
async def mock_redis() -> AsyncGenerator[MagicMock, None]:
    """
    提供 Mock Redis 客户端。

    适用于不需要真实 Redis 的单元测试。
    """
    mock = MagicMock(spec=Redis)
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock(return_value=True)
    mock.delete = AsyncMock(return_value=1)
    mock.exists = AsyncMock(return_value=False)
    mock.expire = AsyncMock(return_value=True)
    mock.ttl = AsyncMock(return_value=-1)
    mock.incr = AsyncMock(return_value=1)
    mock.decr = AsyncMock(return_value=1)
    mock.keys = AsyncMock(return_value=[])
    mock.flushdb = AsyncMock()
    mock.ping = AsyncMock(return_value=True)
    mock.aclose = AsyncMock()

    yield mock


# ==========================================
# 8. FastAPI 测试客户端 Fixtures
# ==========================================


@pytest.fixture
def app():
    """
    创建测试用的 FastAPI 应用实例。

    使用测试配置而非生产配置。
    """
    # Mock 配置
    with patch("internal.config.settings") as mock_settings:
        # 配置基础属性
        mock_settings.DEBUG = True
        mock_settings.APP_ENV = "test"
        mock_settings.JWT_SECRET = "test-jwt-secret-key-for-testing-only"
        mock_settings.JWT_ALGORITHM = "HS256"
        mock_settings.ACCESS_TOKEN_EXPIRE_SECONDS = 3600
        mock_settings.BACKEND_CORS_ORIGINS = ["*"]
        mock_settings.DB_ECHO = False
        mock_settings.SLOW_SQL_THRESHOLD = 0.5
        mock_settings.DB_TYPE = "mysql"
        mock_settings.sqlalchemy_database_uri = TEST_DATABASE_URL
        mock_settings.redis_url = TEST_REDIS_URL

        # 动态导入并创建 app
        from internal.app import create_app

        app_instance = create_app()
        yield app_instance


@pytest.fixture
def client(app) -> Generator[TestClient, None, None]:
    """
    创建同步测试客户端。

    适用于简单的 API 测试。
    """
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest_asyncio.fixture
async def async_client(app) -> AsyncGenerator[AsyncClient, None]:
    """
    创建异步测试客户端。

    适用于需要异步操作的 API 测试。
    """
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


# ==========================================
# 9. 通用测试数据 Fixtures
# ==========================================


@pytest.fixture
def sample_user_data() -> dict[str, Any]:
    """提供测试用的用户数据"""
    return {
        "username": "testuser",
        "email": "test@example.com",
        "password": "TestPassword123!",
    }


@pytest.fixture
def sample_users_data() -> list[dict[str, Any]]:
    """提供批量测试用户数据"""
    return [
        {"username": f"user_{i}", "email": f"user_{i}@example.com"} for i in range(5)
    ]


# ==========================================
# 10. 工具函数 Fixtures
# ==========================================


@pytest.fixture
def reset_id_counter():
    """重置 Mock ID 计数器的 fixture"""

    def _reset(start: int = 0):
        global _id_counter
        _id_counter = start

    return _reset


@pytest.fixture
def freeze_time():
    """
    冻结时间的 fixture。

    用于测试时间相关的功能。
    """
    from unittest.mock import patch

    frozen_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

    with patch("datetime.datetime") as mock_dt:
        mock_dt.utcnow.return_value = frozen_time.replace(tzinfo=None)
        mock_dt.now.return_value = frozen_time
        mock_dt.side_effect = lambda *args, **kwargs: (
            datetime(*args, **kwargs) if args else frozen_time
        )
        yield frozen_time


# ==========================================
# 11. 模块导入 Helper
# ==========================================


@pytest.fixture(scope="session")
def import_modules():
    """
    预加载常用模块的 fixture。

    在 session 开始时预加载，加速后续测试。
    """
    # 预加载常用的包
    import orjson

    return {
        "orjson": orjson,
    }


# ==========================================
# 12. 日志 Mock Fixture
# ==========================================


@pytest.fixture
def logger_mock() -> MagicMock:
    """提供 logger mock 对象，用于验证日志调用"""
    return mock_logger


@pytest.fixture(autouse=True)
def reset_logger_mock():
    """每个测试前重置 logger mock"""
    mock_logger.reset_mock()
    yield
    mock_logger.reset_mock()


# ==========================================
# 13. 配置 Mock Fixture
# ==========================================


@pytest.fixture
def mock_config():
    """
    提供 Mock 配置对象的 fixture。

    可以自定义配置值进行测试。
    """
    config = MagicMock()
    config.DEBUG = True
    config.APP_ENV = "test"
    config.JWT_SECRET = "test-secret"
    config.JWT_ALGORITHM = "HS256"
    config.ACCESS_TOKEN_EXPIRE_SECONDS = 3600
    config.DB_ECHO = False
    config.SLOW_SQL_THRESHOLD = 0.5

    return config


# ==========================================
# 14. Clean Up
# ==========================================


@pytest.fixture(autouse=True)
def cleanup_after_test():
    """
    自动清理 fixture，在每个测试后执行。

    用于清理测试过程中可能产生的全局状态。
    """
    yield

    # 重置配置
    try:
        from internal.config import reset_settings

        reset_settings()
    except ImportError, TypeError:
        pass

    # 重置数据库连接
    try:
        from internal.infra.database import reset_async_db

        reset_async_db()
    except ImportError, TypeError:
        pass

    # 重置 Redis 连接
    try:
        from internal.infra.redis import reset_async_redis

        reset_async_redis()
    except ImportError, TypeError:
        pass
