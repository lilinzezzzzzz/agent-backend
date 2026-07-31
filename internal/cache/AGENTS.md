# AGENTS.md

适用于 `internal/cache/`。

## 层职责

业务缓存层。封装 Redis 对特定业务域的读写，例如 auth token 的 key 约定、TTL 策略、元数据序列化。

- 只承接**业务语义**的缓存访问，不做通用 Redis 薄包装（通用操作直接使用 `pkg.redis.RedisClient`）。
- 按业务域拆文件：`auth.py`、`user.py`、`captcha.py` …，避免单个 `cache.py` 堆成巨石。
- 不承担持久化数据访问，ORM 相关走 `internal/dao/`。

## 编码约定

- 每个业务域一个 `XxxCache` 类，构造函数接受 `RedisClient` 实例，便于测试注入。
- Key 拼接、TTL、序列化细节封装为**私有静态方法**（如 `_token_key`），对外只暴露业务语义方法。
- 对外方法名使用业务动词，如 `save_user_session`、`revoke_user_session`、`get_user_metadata`，不暴露底层 `push_to_list`、`set_dict` 这类 Redis 原语。
- 读不到时返回 `None` 或空集合，不抛异常；写入异常按 Redis 客户端原语冒泡，由 Service/Controller 决定是否转为 `AppException`。
- factory 采用模块级懒加载单例（`_xxx_cache: XxxCache | None = None` + `new_xxx_cache()`）。

## 代码最小正确形态

```python
"""Auth 业务缓存"""

from internal.infra.redis.connection import redis_client
from pkg.toolkit.json import orjson_dumps, orjson_loads
from pkg.redis import RedisClient


class AuthCache:
    def __init__(self, redis_cli: RedisClient):
        self._redis = redis_cli

    @staticmethod
    def _token_key(token: str) -> str:
        return f"token:{token}"

    async def get_user_metadata(self, token: str) -> dict | None:
        val = await self._redis.get_value(self._token_key(token))
        return orjson_loads(val) if val is not None else None

    async def set_user_metadata(self, token: str, metadata: dict, ex: int | None = None) -> bool:
        return await self._redis.set_value(self._token_key(token), orjson_dumps(metadata), ex=ex)


_auth_cache: AuthCache | None = None


def new_auth_cache() -> AuthCache:
    global _auth_cache
    if _auth_cache is None:
        _auth_cache = AuthCache(redis_cli=redis_client)
    return _auth_cache
```

## 验证重点

- 修改 key 前缀、TTL、序列化格式属于跨服务兼容性变更：必须同步检查依赖方（认证 dependency、Service、Celery 任务）并评估历史数据迁移或过期策略。
- 新增业务域缓存时，优先复用 `pkg.redis` 已有原语，不要再实现一层 Redis 封装。
- 单元测试使用 fake/mock `RedisClient`，覆盖 miss、hit、TTL 过期、并发写入顺序等边界。
