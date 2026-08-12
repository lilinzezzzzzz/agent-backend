from typing import Any
from uuid import UUID

import pytest

from internal.cache.agent_action import AgentActionCache

TEST_USER_ID = UUID("00000000-0000-7000-8000-000000000999")


class FakeRedisClient:
    def __init__(self):
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def set_dict(
        self, key: str, value: dict[str, object], ex: int | None = None
    ) -> bool:
        self.calls.append(("set_dict", (key, value), {"ex": ex}))
        return True

    async def get_dict(self, key: str) -> dict[str, object] | None:
        self.calls.append(("get_dict", (key,), {}))
        return None

    async def delete_key(self, key: str) -> int:
        self.calls.append(("delete_key", (key,), {}))
        return 1

    async def acquire_lock(self, lock_key: str, expire_ms: int, timeout_ms: int) -> str:
        self.calls.append(
            (
                "acquire_lock",
                (lock_key,),
                {"expire_ms": expire_ms, "timeout_ms": timeout_ms},
            )
        )
        return "lock-id"

    async def release_lock(self, lock_key: str, identifier: str) -> bool:
        self.calls.append(("release_lock", (lock_key, identifier), {}))
        return True


@pytest.mark.asyncio
async def test_agent_action_cache_uses_confirmation_token_for_execution_lock() -> None:
    redis = FakeRedisClient()
    cache = AgentActionCache(redis_cli=redis)

    identifier = await cache.acquire_confirmation_lock(token="confirmation-token")
    released = await cache.release_confirmation_lock(
        token="confirmation-token",
        identifier=identifier,
    )

    assert released is True
    assert redis.calls == [
        (
            "acquire_lock",
            ("agent_action:confirmation_lock:confirmation-token",),
            {"expire_ms": 10_000, "timeout_ms": 3_000},
        ),
        (
            "release_lock",
            ("agent_action:confirmation_lock:confirmation-token", "lock-id"),
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_agent_action_cache_hashes_idempotency_key_and_passes_ttl() -> None:
    redis = FakeRedisClient()
    cache = AgentActionCache(redis_cli=redis)

    saved = await cache.save_idempotency_result(
        user_id=TEST_USER_ID,
        idempotency_key="client-visible-idempotency-key",
        payload={"status": "queued"},
        expires_in_seconds=86400,
    )

    assert saved is True
    method, args, kwargs = redis.calls[0]
    assert method == "set_dict"
    assert args[0].startswith(f"agent_action:idempotency:{TEST_USER_ID}:")
    assert "client-visible-idempotency-key" not in args[0]
    assert kwargs == {"ex": 86400}
