"""Agent 待确认动作与幂等结果缓存。"""

from hashlib import sha256

from internal.infra.redis.connection import redis_client
from pkg.redis import RedisClient


class AgentActionCache:
    """封装 Agent 副作用动作的确认 token、幂等结果和执行锁。

    confirmation_result 按 token 保存，用于阻止同一个待确认动作被不同幂等键重复执行；
    idempotency_result 按 user_id + idempotency_key 保存，用于覆盖客户端对同一次确认
    请求的重试。Redis key 中只保存幂等键摘要，避免把客户端原始重试键暴露到 key 空间。
    """

    def __init__(self, redis_cli: RedisClient):
        self._redis = redis_cli

    @staticmethod
    def _confirmation_key(token: str) -> str:
        return f"agent_action:confirmation:{token}"

    @staticmethod
    def _confirmation_result_key(token: str) -> str:
        return f"agent_action:confirmation_result:{token}"

    @staticmethod
    def _idempotency_digest(idempotency_key: str) -> str:
        return sha256(idempotency_key.encode("utf-8")).hexdigest()

    @classmethod
    def _idempotency_key(cls, user_id: int, idempotency_key: str) -> str:
        digest = cls._idempotency_digest(idempotency_key)
        return f"agent_action:idempotency:{user_id}:{digest}"

    @staticmethod
    def _confirmation_lock_key(token: str) -> str:
        return f"agent_action:confirmation_lock:{token}"

    async def save_pending_action(
        self, *, token: str, payload: dict[str, object], expires_in_seconds: int
    ) -> bool:
        """保存待用户确认的动作。"""
        return await self._redis.set_dict(
            self._confirmation_key(token),
            payload,
            ex=expires_in_seconds,
        )

    async def get_pending_action(self, *, token: str) -> dict[str, object] | None:
        """读取待确认动作，未命中或已过期时返回 None。"""
        return await self._redis.get_dict(self._confirmation_key(token))

    async def delete_pending_action(self, *, token: str) -> int:
        """删除已执行的待确认动作。"""
        return await self._redis.delete_key(self._confirmation_key(token))

    async def save_confirmation_result(
        self, *, token: str, payload: dict[str, object], expires_in_seconds: int
    ) -> bool:
        """保存确认动作的执行结果，防止不同幂等键重复执行同一动作。"""
        return await self._redis.set_dict(
            self._confirmation_result_key(token),
            payload,
            ex=expires_in_seconds,
        )

    async def get_confirmation_result(self, *, token: str) -> dict[str, object] | None:
        """读取确认动作的执行结果。"""
        return await self._redis.get_dict(self._confirmation_result_key(token))

    async def save_idempotency_result(
        self,
        *,
        user_id: int,
        idempotency_key: str,
        payload: dict[str, object],
        expires_in_seconds: int,
    ) -> bool:
        """保存副作用动作的幂等执行结果，key 中使用 idempotency_key 摘要。"""
        return await self._redis.set_dict(
            self._idempotency_key(user_id, idempotency_key),
            payload,
            ex=expires_in_seconds,
        )

    async def get_idempotency_result(
        self, *, user_id: int, idempotency_key: str
    ) -> dict[str, object] | None:
        """读取副作用动作的幂等执行结果。"""
        return await self._redis.get_dict(
            self._idempotency_key(user_id, idempotency_key)
        )

    async def acquire_confirmation_lock(self, *, token: str) -> str:
        """获取确认动作执行锁。"""
        return await self._redis.acquire_lock(
            self._confirmation_lock_key(token),
            expire_ms=10_000,
            timeout_ms=3_000,
        )

    async def release_confirmation_lock(self, *, token: str, identifier: str) -> bool:
        """释放确认动作执行锁。"""
        return await self._redis.release_lock(
            self._confirmation_lock_key(token),
            identifier,
        )


_agent_action_cache: AgentActionCache | None = None


def new_agent_action_cache() -> AgentActionCache:
    """依赖注入：获取 AgentActionCache 单例。"""
    global _agent_action_cache
    if _agent_action_cache is None:
        _agent_action_cache = AgentActionCache(redis_cli=redis_client)
    return _agent_action_cache
