"""Auth 业务缓存：用户会话 token 与元数据"""

from functools import cache
from uuid import UUID

from internal.infra.redis.connection import redis_client
from pkg.logger import logger
from pkg.toolkit.json import orjson_dumps, orjson_loads
from pkg.redis import RedisClient


class AuthCache:
    """Auth 领域的 Redis 缓存访问。

    只暴露业务语义方法，key 拼接和序列化细节对调用方透明。
    """

    def __init__(self, redis_cli: RedisClient):
        self._redis = redis_cli

    # ---------- key 约定 ----------

    @staticmethod
    def _token_key(token: str) -> str:
        return f"token:{token}"

    @staticmethod
    def _user_token_list_key(user_id: UUID) -> str:
        return f"token_list:{user_id}"

    # ---------- metadata ----------

    async def get_user_metadata(self, token: str) -> dict | None:
        """按 token 读取用户元数据，未命中返回 None。"""
        val = await self._redis.get_value(self._token_key(token))
        if val is None:
            logger.warning("Token verification failed: token not found")
            return None

        return orjson_loads(val)

    async def set_user_metadata(
        self, token: str, metadata: dict, ex: int | None = None
    ) -> bool:
        """按 token 存储用户元数据。"""
        json_str = orjson_dumps(metadata)
        return await self._redis.set_value(self._token_key(token), json_str, ex=ex)

    async def delete_user_metadata(self, token: str) -> int:
        """按 token 删除用户元数据，返回删除数量。"""
        return await self._redis.delete_key(self._token_key(token))

    # ---------- token list ----------

    async def get_user_token_list(self, user_id: UUID) -> list[str]:
        """读取某用户的有效 token 列表。"""
        val = await self._redis.get_list(self._user_token_list_key(user_id))
        if not val:
            logger.warning(
                f"Token verification failed: token list not found, user_id: {user_id}"
            )
            return []

        return val

    async def add_user_token(self, user_id: UUID, token: str) -> int:
        """向用户 token 列表追加新 token。"""
        return await self._redis.push_to_list(self._user_token_list_key(user_id), token)

    async def remove_user_token(self, user_id: UUID, token: str) -> int:
        """从用户 token 列表移除指定 token。"""
        return await self._redis.remove_from_list(
            self._user_token_list_key(user_id), token
        )

    # ---------- 组合操作 ----------

    async def save_user_session(
        self, user_id: UUID, token: str, metadata: dict, ex: int | None = None
    ) -> None:
        """保存一次会话：写入 metadata 并把 token 追加到用户 token 列表。"""
        await self.set_user_metadata(token, metadata, ex=ex)
        await self.add_user_token(user_id, token)

    async def revoke_user_session(self, user_id: UUID, token: str) -> int:
        """
        撤销一次会话：
        删除 metadata 并从用户 token 列表中移除。返回 metadata 删除数量。
        """
        deleted = await self.delete_user_metadata(token)
        if deleted > 0:
            await self.remove_user_token(user_id, token)
        return deleted


@cache
def new_auth_cache() -> AuthCache:
    """依赖注入：获取 AuthCache 单例"""
    return AuthCache(redis_cli=redis_client)
