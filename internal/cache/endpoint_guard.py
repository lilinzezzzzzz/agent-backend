"""Endpoint guard 规则缓存访问。"""

from internal.infra.redis.connection import redis_client
from pkg.redis import RedisClient


class EndpointGuardCache:
    """Endpoint guard 领域的 Redis 缓存访问。"""

    def __init__(self, redis_cli: RedisClient):
        self._redis = redis_cli

    async def get_rules_payload(self, key: str) -> str | None:
        """读取 endpoint guard 规则 JSON，未命中返回 None。"""
        return await self._redis.get_value(key)

    async def set_rules_payload(
        self, key: str, payload: str, ex: int | None = None
    ) -> bool:
        """写入 endpoint guard 规则 JSON。"""
        return await self._redis.set_value(key, payload, ex=ex)


_endpoint_guard_cache: EndpointGuardCache | None = None


def new_endpoint_guard_cache() -> EndpointGuardCache:
    """依赖注入：获取 EndpointGuardCache 单例。"""
    global _endpoint_guard_cache
    if _endpoint_guard_cache is None:
        _endpoint_guard_cache = EndpointGuardCache(redis_cli=redis_client)
    return _endpoint_guard_cache
