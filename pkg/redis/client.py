import functools
import json
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import anyio
from redis.asyncio import Redis

from pkg.ids import uuid7_unique_str_id
from pkg.toolkit.json import orjson_dumps, orjson_loads

SessionProvider = Callable[[], AbstractAsyncContextManager[Redis]]


class RedisOperationError(Exception):
    """Redis 操作异常"""

    pass


def handle_redis_exception(func):
    """
    装饰器：统一处理 Redis 操作的异常捕获和日志记录
    """

    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        try:
            return await func(self, *args, **kwargs)
        except RedisOperationError:
            # 已经是 RedisOperationError，直接抛出避免重复包装
            raise
        except Exception as e:
            raise RedisOperationError(f"Redis error in '{func.__name__}': {repr(e)} | args: {args}") from e

    return wrapper


class RedisClient:
    """Redis 客户端工具类"""

    def __init__(self, session_provider: SessionProvider):
        self.session_provider = session_provider

    @handle_redis_exception
    async def set_value(self, key: str, value: Any, ex: int | None = None) -> bool:
        """设置键值对，可选过期时间（秒）"""
        async with self.session_provider() as redis:
            result = await redis.set(key, value, ex=ex)
            # redis.set() 返回 True 或 None
            return result is True

    @handle_redis_exception
    async def get_value(self, key: str) -> str | None:
        """获取键对应的值"""
        async with self.session_provider() as redis:
            value = await redis.get(key)
            if value is None:
                return None
            if isinstance(value, bytes):
                return value.decode("utf-8")

            return value

    @handle_redis_exception
    async def set_dict(self, key: str, value: dict, ex: int | None = None) -> bool:
        """设置字典类型的值，自动 JSON 序列化"""
        json_str = orjson_dumps(value)
        return await self.set_value(key, json_str, ex=ex)

    @handle_redis_exception
    async def get_dict(self, key: str) -> dict | None:
        """获取字典类型的值，自动 JSON 反序列化"""
        value = await self.get_value(key)
        if value is None:
            return None
        try:
            return orjson_loads(value)
        except json.JSONDecodeError as e:
            raise RedisOperationError(f"Failed to decode dict from key '{key}': {e}") from e

    @handle_redis_exception
    async def set_list(self, key: str, value: list, ex: int | None = None) -> bool:
        """设置列表类型的值，自动 JSON 序列化"""
        json_str = orjson_dumps(value)
        return await self.set_value(key, json_str, ex=ex)

    @handle_redis_exception
    async def get_list_value(self, key: str) -> list | None:
        """获取列表类型的值，自动 JSON 反序列化"""
        value = await self.get_value(key)
        if value is None:
            return None
        try:
            return orjson_loads(value)
        except json.JSONDecodeError as e:
            raise RedisOperationError(f"Failed to decode list from key '{key}': {e}") from e

    @handle_redis_exception
    async def delete_key(self, key: str) -> int:
        """删除键，返回删除的键数量"""
        async with self.session_provider() as redis:
            return await redis.delete(key)

    @handle_redis_exception
    async def set_expiry(self, key: str, ex: int) -> bool:
        """设置键的过期时间（秒）"""
        async with self.session_provider() as redis:
            return await redis.expire(key, ex)

    @handle_redis_exception
    async def key_exists(self, key: str) -> bool:
        """检查键是否存在"""
        async with self.session_provider() as redis:
            return await redis.exists(key) > 0

    @handle_redis_exception
    async def get_ttl(self, key: str) -> int:
        """获取键的剩余过期时间（秒），-1 表示永不过期，-2 表示不存在"""
        async with self.session_provider() as redis:
            return await redis.ttl(key)

    @handle_redis_exception
    async def set_hash(self, name: str, key: str, value: Any) -> int:
        """
        设置哈希表字段的值。
        返回值：1 表示新增字段，0 表示更新已存在字段（操作均成功）
        """
        async with self.session_provider() as redis:
            return await redis.hset(name, key, value)

    @handle_redis_exception
    async def get_hash(self, name: str, key: str) -> str | None:
        """获取哈希表中指定字段的值"""
        async with self.session_provider() as redis:
            value = await redis.hget(name, key)
            return value.decode() if isinstance(value, bytes) else value

    @handle_redis_exception
    async def push_to_list(self, name: str, value: Any, direction: str = "right") -> int:
        """
        向列表添加元素。
        direction: 'left' 从左侧插入，'right' 从右侧插入（默认）
        返回列表当前长度
        """
        async with self.session_provider() as redis:
            if direction == "left":
                return await redis.lpush(name, value)
            return await redis.rpush(name, value)

    @handle_redis_exception
    async def get_list(self, name: str) -> list[str]:
        """
        获取列表所有值，并强制转换为字符串列表。
        """
        async with self.session_provider() as redis:
            values = await redis.lrange(name, 0, -1)
            if not values:
                return []
            # 统一解码处理
            return [v.decode() if isinstance(v, bytes) else v for v in values]

    @handle_redis_exception
    async def left_pop_list(self, name: str) -> str | None:
        """从列表左侧弹出元素"""
        async with self.session_provider() as redis:
            value = await redis.lpop(name)
            return value.decode() if isinstance(value, bytes) else value

    async def release_lock(self, lock_key: str, identifier: str) -> bool:
        """
        释放分布式锁。
        只有持有正确 identifier 的调用者才能释放锁。

        Args:
            lock_key: 锁的键名
            identifier: 获取锁时返回的唯一标识符

        Returns:
            True 表示成功释放，False 表示锁不存在或不属于该 identifier

        Raises:
            RedisOperationError: Redis 操作失败时抛出
        """
        unlock_script = """
        if redis.call('GET', KEYS[1]) == ARGV[1] then
            return redis.call('DEL', KEYS[1])
        end
        return 0
        """
        try:
            async with self.session_provider() as redis:
                result = await redis.eval(unlock_script, 1, lock_key, identifier)
            return bool(result)
        except Exception as e:
            raise RedisOperationError(f"Failed to release lock {lock_key}: {e}") from e

    async def acquire_lock(
        self,
        lock_key: str,
        expire_ms: int = 10000,
        timeout_ms: int = 5000,
        retry_interval_ms: int = 100,
    ) -> str:
        """
        获取分布式锁。

        Args:
            lock_key: 锁的键名
            expire_ms: 锁的过期时间（毫秒），默认 10 秒
            timeout_ms: 获取锁的超时时间（毫秒），默认 5 秒
            retry_interval_ms: 重试间隔（毫秒），默认 100ms

        Returns:
            成功返回锁的唯一标识符（用于释放锁）

        Raises:
            RedisOperationError: 获取锁超时或 Redis 操作失败时抛出
        """

        identifier = uuid7_unique_str_id()
        start_time = time.perf_counter()
        timeout_seconds = timeout_ms / 1000
        retry_interval_seconds = retry_interval_ms / 1000

        try:
            while (time.perf_counter() - start_time) < timeout_seconds:
                async with self.session_provider() as redis:
                    # 使用 Redis 原生 SET NX PX 命令，比 Lua 脚本更简洁高效
                    acquired = await redis.set(lock_key, identifier, nx=True, px=expire_ms)

                if acquired:
                    return identifier

                await anyio.sleep(retry_interval_seconds)
        except Exception as e:
            raise RedisOperationError(f"Error acquiring lock {lock_key}: {e}") from e

        raise RedisOperationError(f"Timeout acquiring lock {lock_key}, timeout_ms: {timeout_ms}")

    @handle_redis_exception
    async def batch_delete_keys(self, keys: list[str]) -> int:
        """批量删除键，返回成功删除的键数量"""
        if not keys:
            return 0
        async with self.session_provider() as redis:
            return await redis.delete(*keys)

    @handle_redis_exception
    async def remove_from_list(self, name: str, value: str) -> int:
        """
        从列表中移除指定元素。
        返回列表长度（移除后的）。
        """
        async with self.session_provider() as redis:
            # 使用 LREM 移除所有等于 value 的元素
            # count=0 表示移除所有匹配的元素
            return await redis.lrem(name, 0, value)
