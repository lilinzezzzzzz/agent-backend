"""
Pytest 配置文件 (conftest.py)

提供测试运行所需的共享 fixtures、hooks 和配置。
所有 fixtures 和 hooks 在此文件定义后，可在任意测试文件中直接使用。

主要功能：
1. 使用真实基础模块，按测试隔离日志和请求上下文
2. 提供数据库测试 fixtures（内存 SQLite）
3. 提供 Redis 测试 fixtures
4. 提供 FastAPI 测试客户端
5. 配置 pytest-asyncio 后端
"""

import inspect
import os
import subprocess
import sys
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
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


def pytest_configure(config: pytest.Config) -> None:
    """
    pytest 配置钩子，在测试开始前执行。

    用于注册自定义 markers。
    """
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
def isolate_logging(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """绑定真实 Loguru；每个测试自行配置输出，结束后清理自己添加的 sink。"""
    from loguru import logger

    import pkg.logger as logger_module

    monkeypatch.setattr(logger_module, "_logger", logger)
    monkeypatch.setattr(logger_module, "_logger_manager", None)
    logger.remove()
    try:
        yield
    finally:
        logger.complete()
        logger.remove()


@pytest.fixture(autouse=True)
def isolate_settings_cache() -> Generator[None, None, None]:
    """独立清理配置缓存，不依赖 factory_cache 或其他业务模块被导入。"""
    config_module = sys.modules.get("internal.config")
    if config_module is not None:
        config_module.reset_settings()
    try:
        yield
    finally:
        # 配置也可能是在当前测试中首次导入的。
        config_module = sys.modules.get("internal.config")
        if config_module is not None:
            config_module.reset_settings()


@pytest.fixture(autouse=True)
def isolate_request_context() -> Generator[None, None, None]:
    """为每个测试建立独立上下文，离开时恢复调用方原状态。"""
    from pkg.request_context import scoped_values

    with scoped_values():
        yield


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


@pytest.fixture(scope="session")
def postgresql_migration_sql() -> str:
    """从迁移历史按需生成 SQL，不读取应用配置或连接数据库。"""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(PROJECT_ROOT / "alembic.ini"),
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return result.stdout


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
    # 使用真实 ORM 基类，在内存数据库中建表。
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
    from internal.config import AppSettings

    # Mock 配置
    with patch("internal.config.settings") as mock_settings:
        mock_settings.debug = True
        mock_settings.app_env = "test"
        mock_settings.app = AppSettings(
            title="test-agent-backend",
            version="0.1.0",
            docs_enabled=True,
            redoc_enabled=True,
            openapi_enabled=True,
            docs_path="/docs",
            redoc_path="/redoc",
            openapi_path="/openapi.json",
        )
        mock_settings.secrets = SimpleNamespace(
            jwt_secret=SecretStr("test-jwt-secret-key-for-testing-only"),
            aes_secret=SecretStr("test-aes-secret"),
            db_password=SecretStr(""),
            db_read_password=None,
            redis_password=SecretStr(""),
            wechat_app_id="",
            wechat_app_secret=SecretStr(""),
            llm_mimo_api_key=SecretStr(""),
            llm_deepseek_api_key=SecretStr(""),
            embedding_api_key=SecretStr(""),
        )
        mock_settings.auth = SimpleNamespace(
            jwt=SimpleNamespace(algorithm="HS256"),
            access_token=SimpleNamespace(expire_seconds=3600),
        )
        mock_settings.cors = SimpleNamespace(origins=["*"])
        mock_settings.db = SimpleNamespace(type="sqlite", echo=False)
        mock_settings.log = SimpleNamespace(
            format="text",
            base_dir=None,
            write_to_file=False,
            write_to_console=True,
        )
        mock_settings.otel = SimpleNamespace(
            tracing_enabled=False,
            operation_spans_enabled=True,
            metrics_enabled=False,
            logs_enabled=False,
            auto_configure=False,
            service_name="test-agent-backend",
            exporter_otlp_traces_endpoint=None,
            exporter_otlp_timeout_seconds=10,
        )
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
def logger_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """按需替换 logger 的实际目标，不替换模块或已导入的代理。"""
    import pkg.logger as logger_module

    logger = MagicMock()
    monkeypatch.setattr(logger_module, "_logger", logger)
    return logger


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
    config.debug = True
    config.app_env = "test"
    config.secrets = SimpleNamespace(jwt_secret=SecretStr("test-secret"))
    config.auth = SimpleNamespace(
        jwt=SimpleNamespace(algorithm="HS256"),
        access_token=SimpleNamespace(expire_seconds=3600),
    )
    config.db = SimpleNamespace(echo=False)

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

    # 仅清理当前进程已使用的资源，不为清理而导入业务模块。
    for module_name, reset_name in (
        ("tests.support.factory_cache", "reset_factory_caches"),
        ("internal.infra.database", "reset_async_db"),
        ("internal.infra.redis", "reset_async_redis"),
    ):
        module = sys.modules.get(module_name)
        if module is not None:
            getattr(module, reset_name)()
