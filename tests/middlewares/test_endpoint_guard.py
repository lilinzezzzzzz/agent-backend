from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from internal.core import errors
from internal.middlewares.endpoint_guard import (
    ASGIEndpointGuardMiddleware,
    EndpointGuardProviderError,
    EndpointGuardRule,
    RedisEndpointGuardRuleProvider,
    parse_endpoint_guard_rules,
)
from internal.middlewares.recorder import ASGIRecordMiddleware
from pkg.toolkit.json import orjson_dumps


class FakeRuleProvider:
    def __init__(self, rules: Sequence[EndpointGuardRule]):
        self.rules = tuple(rules)
        self.calls = 0

    async def get_rules(self) -> Sequence[EndpointGuardRule]:
        self.calls += 1
        return self.rules


class FailingRuleProvider:
    def __init__(self):
        self.calls = 0

    async def get_rules(self) -> Sequence[EndpointGuardRule]:
        self.calls += 1
        raise AssertionError("provider should not be called")


class FakeEndpointGuardCache:
    def __init__(self, values: list[object]):
        self.values = values
        self.calls = 0

    async def get_rules_payload(self, _key: str) -> str | None:
        self.calls += 1
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        if value is None or isinstance(value, str):
            return value
        raise TypeError(f"unsupported fake value: {value!r}")


def _rule(**kwargs) -> EndpointGuardRule:
    defaults = {
        "id": "rule-1",
        "methods": ["GET"],
        "path": "/v1/orders",
        "match_type": "exact",
    }
    defaults.update(kwargs)
    return EndpointGuardRule.model_validate(defaults)


def _build_app(
    provider: FakeRuleProvider | FailingRuleProvider,
    *,
    enabled: bool = True,
) -> tuple[FastAPI, SimpleNamespace]:
    app = FastAPI()
    state = SimpleNamespace(handler_calls=0)

    @app.api_route("/v1/orders", methods=["GET", "HEAD", "OPTIONS", "POST"])
    async def orders() -> dict[str, bool]:
        state.handler_calls += 1
        return {"ok": True}

    @app.get("/v1/orders/{order_id}")
    async def order_detail(order_id: str) -> dict[str, str]:
        state.handler_calls += 1
        return {"order_id": order_id}

    @app.get("/v1/admin/users")
    async def admin_users() -> dict[str, bool]:
        state.handler_calls += 1
        return {"ok": True}

    @app.get("/v1/administrator")
    async def administrator() -> dict[str, bool]:
        state.handler_calls += 1
        return {"ok": True}

    app.add_middleware(ASGIEndpointGuardMiddleware, provider=provider, enabled=enabled)
    app.add_middleware(ASGIRecordMiddleware)
    return app, state


@pytest.mark.asyncio
async def test_endpoint_guard_blocks_exact_match_and_keeps_response_envelope() -> None:
    provider = FakeRuleProvider(
        [
            _rule(
                error="disabled",
                message="接口已临时关闭",
                reason="incident-test",
            )
        ]
    )
    app, state = _build_app(provider)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/orders")

    assert response.status_code == 200
    assert response.json()["code"] == errors.EndpointDisabled.code
    assert "接口已临时关闭" in response.json()["message"]
    assert provider.calls == 1
    assert state.handler_calls == 0


@pytest.mark.asyncio
async def test_endpoint_guard_passes_when_disabled() -> None:
    provider = FailingRuleProvider()
    app, state = _build_app(provider, enabled=False)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/orders")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert provider.calls == 0
    assert state.handler_calls == 1


@pytest.mark.asyncio
async def test_endpoint_guard_skips_options_requests() -> None:
    provider = FailingRuleProvider()
    app, state = _build_app(provider)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.options("/v1/orders")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert provider.calls == 0
    assert state.handler_calls == 1


@pytest.mark.asyncio
async def test_endpoint_guard_passes_when_method_does_not_match() -> None:
    provider = FakeRuleProvider([_rule(methods=["POST"])])
    app, state = _build_app(provider)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/orders")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert provider.calls == 1
    assert state.handler_calls == 1


@pytest.mark.asyncio
async def test_endpoint_guard_treats_head_as_get_for_matching() -> None:
    provider = FakeRuleProvider([_rule(methods=["GET"])])
    app, state = _build_app(provider)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.head("/v1/orders")

    assert response.status_code == 200
    assert provider.calls == 1
    assert state.handler_calls == 0


@pytest.mark.asyncio
async def test_endpoint_guard_prefix_match_uses_path_segment_boundary() -> None:
    provider = FakeRuleProvider(
        [
            _rule(
                id="admin-prefix",
                methods=["GET"],
                path="/v1/admin",
                match_type="prefix",
                error="forbidden",
            )
        ]
    )
    app, state = _build_app(provider)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        blocked_response = await client.get("/v1/admin/users")
        passed_response = await client.get("/v1/administrator")

    assert blocked_response.json()["code"] == errors.Forbidden.code
    assert passed_response.json() == {"ok": True}
    assert provider.calls == 2
    assert state.handler_calls == 1


@pytest.mark.asyncio
async def test_endpoint_guard_template_match_blocks_single_path_segment() -> None:
    provider = FakeRuleProvider(
        [
            _rule(
                id="order-template",
                path="/v1/orders/{order_id}",
                match_type="template",
                error="unavailable",
            )
        ]
    )
    app, state = _build_app(provider)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/orders/123")

    assert response.json()["code"] == errors.ServiceUnavailable.code
    assert provider.calls == 1
    assert state.handler_calls == 0


@pytest.mark.asyncio
async def test_endpoint_guard_ignores_disabled_and_expired_rules() -> None:
    provider = FakeRuleProvider(
        [
            _rule(id="disabled", enabled=False),
            _rule(id="expired", expires_at=datetime.now(UTC) - timedelta(seconds=1)),
        ]
    )
    app, state = _build_app(provider)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/orders")

    assert response.json() == {"ok": True}
    assert provider.calls == 1
    assert state.handler_calls == 1


@pytest.mark.asyncio
async def test_redis_rule_provider_uses_last_good_rules_after_failure() -> None:
    first_payload = orjson_dumps(
        [
            {
                "id": "redis-rule",
                "methods": ["GET"],
                "path": "/v1/orders",
                "match_type": "exact",
            }
        ]
    )
    cache = FakeEndpointGuardCache([first_payload, RuntimeError("redis down")])
    provider = RedisEndpointGuardRuleProvider(
        cache=cache,
        redis_key="endpoint_guard:rules",
        cache_ttl_seconds=0,
        fail_open=True,
    )

    first_rules = await provider.get_rules()
    fallback_rules = await provider.get_rules()

    assert [rule.id for rule in first_rules] == ["redis-rule"]
    assert [rule.id for rule in fallback_rules] == ["redis-rule"]
    assert cache.calls == 2


@pytest.mark.asyncio
async def test_redis_rule_provider_fail_open_without_last_good_rules() -> None:
    cache = FakeEndpointGuardCache([RuntimeError("redis down")])
    provider = RedisEndpointGuardRuleProvider(
        cache=cache,
        redis_key="endpoint_guard:rules",
        cache_ttl_seconds=0,
        fail_open=True,
    )

    rules = await provider.get_rules()

    assert rules == ()
    assert cache.calls == 1


@pytest.mark.asyncio
async def test_redis_rule_provider_raises_when_fail_closed_without_last_good_rules() -> (
    None
):
    cache = FakeEndpointGuardCache([RuntimeError("redis down")])
    provider = RedisEndpointGuardRuleProvider(
        cache=cache,
        redis_key="endpoint_guard:rules",
        cache_ttl_seconds=0,
        fail_open=False,
    )

    with pytest.raises(EndpointGuardProviderError):
        await provider.get_rules()


def test_parse_endpoint_guard_rules_validates_payload() -> None:
    with pytest.raises(ValueError, match="endpoint guard rules must be a list"):
        parse_endpoint_guard_rules('{"id":"not-a-list"}')
