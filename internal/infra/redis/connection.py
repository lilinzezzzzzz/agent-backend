"""Redis 连接管理"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from redis.asyncio import ConnectionPool, Redis

from internal.config import settings
from pkg.logger import logger
from pkg.redis import RedisClient
from pkg.lazy_proxy import lazy_proxy

# 全局变量，初始为 None
_redis_pool: ConnectionPool | None = None
_raw_redis: Redis | None = None  # 原始 Redis 客户端
_redis_client: RedisClient | None = None  # 封装后的 Redis 客户端


def _get_redis_client() -> RedisClient:
    """
    获取全局 Redis 客户端实例的 Helper 函数
    替代直接 import redis_client 变量，防止 import 时为 None 的问题
    """
    if _redis_client is None:
        raise RuntimeError("Redis is not initialized. Call init_redis() first.")
    return _redis_client


redis_client = lazy_proxy(_get_redis_client)


def init_async_redis() -> None:
    """
    初始化 Redis 连接池。
    应在 FastAPI lifespan 或 Celery worker_process_init 中调用。
    """
    global _redis_pool, _raw_redis, _redis_client

    logger.info("Initializing Redis connection...")

    if _redis_pool is None:
        # 创建连接池
        _redis_pool = ConnectionPool.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=getattr(settings, "REDIS_MAX_CONNECTIONS", 20),
        )

    # 创建原始 Redis 客户端实例
    if _raw_redis is None:
        _raw_redis = Redis(connection_pool=_redis_pool, decode_responses=True)

    # 初始化封装后的 Redis 客户端
    if _redis_client is None:
        _redis_client = RedisClient(session_provider=get_redis)

    logger.success("Redis initialized successfully.")


async def close_async_redis() -> None:
    """关闭 Redis 连接"""
    global _raw_redis, _redis_pool, _redis_client

    if _raw_redis:
        await _raw_redis.close()
        logger.warning("Redis connection closed.")

    # 清理引用
    _raw_redis = None
    _redis_pool = None
    _redis_client = None


def reset_async_redis() -> None:
    global _raw_redis, _redis_pool, _redis_client

    _raw_redis = None
    _redis_pool = None
    _redis_client = None


@asynccontextmanager
async def get_redis() -> AsyncGenerator[Redis, None]:
    """
    Redis Session 获取上下文管理器
    """
    if _raw_redis is None:
        raise RuntimeError("Redis is not initialized. Call init_redis() first.")

    yield _raw_redis
