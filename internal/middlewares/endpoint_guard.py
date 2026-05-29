"""Endpoint 访问开关中间件。"""

import fnmatch
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import ValidationError, field_validator
from starlette.types import ASGIApp, Receive, Scope, Send

from internal.cache.endpoint_guard import EndpointGuardCache, new_endpoint_guard_cache
from internal.config import settings
from internal.core import AppException, errors
from internal.schemas import IgnoreExtraModel
from pkg.logger import logger, span_context
from pkg.toolkit.json import orjson_loads
from pkg.toolkit.middleware import BaseMiddlewareContext

_SPAN_NAME = "middleware.endpoint_guard"
_ANY_METHOD = "*"
_DEFAULT_RULES_PAYLOAD = "[]"


class EndpointGuardMatchType(StrEnum):
    """Endpoint guard 支持的路径匹配模式。"""

    EXACT = "exact"
    PREFIX = "prefix"
    GLOB = "glob"
    TEMPLATE = "template"


class EndpointGuardError(StrEnum):
    """Endpoint guard 命中规则后的业务错误类型。"""

    DISABLED = "disabled"
    FORBIDDEN = "forbidden"
    UNAVAILABLE = "unavailable"
    NOT_FOUND = "not_found"


class EndpointGuardSource(StrEnum):
    """Endpoint guard 规则来源。"""

    SETTINGS = "settings"
    REDIS = "redis"
    SETTINGS_AND_REDIS = "settings+redis"


class EndpointGuardRule(IgnoreExtraModel):
    """Endpoint guard 访问拒绝规则。"""

    id: str
    enabled: bool = True
    methods: tuple[str, ...] = (_ANY_METHOD,)
    path: str
    match_type: EndpointGuardMatchType = EndpointGuardMatchType.EXACT
    error: EndpointGuardError = EndpointGuardError.DISABLED
    message: str = ""
    reason: str = ""
    expires_at: datetime | None = None

    @field_validator("methods", mode="before")
    @classmethod
    def _parse_methods(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return (_ANY_METHOD,)
        if isinstance(value, str):
            return (value,)
        if isinstance(value, Sequence):
            return tuple(str(item) for item in value)
        raise ValueError("methods must be a string or sequence")

    @field_validator("methods")
    @classmethod
    def _validate_methods(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        methods = tuple(method.strip().upper() for method in value if method.strip())
        if not methods:
            raise ValueError("methods cannot be empty")
        if _ANY_METHOD in methods and len(methods) > 1:
            raise ValueError("methods '*' cannot be combined with other methods")
        return methods

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        path = value.strip()
        if not path.startswith("/"):
            raise ValueError("path must start with '/'")
        return path

    @field_validator("expires_at")
    @classmethod
    def _normalize_expires_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    def is_active(self, *, now: datetime | None = None) -> bool:
        """判断规则当前是否有效。"""
        if not self.enabled:
            return False
        if self.expires_at is None:
            return True
        current = now or datetime.now(UTC)
        return self.expires_at > current


class EndpointGuardProviderError(Exception):
    """Endpoint guard 规则源不可用。"""


class EndpointGuardRuleProvider(Protocol):
    """Endpoint guard 规则提供方。"""

    async def get_rules(self) -> Sequence[EndpointGuardRule]:
        """返回当前 endpoint guard 规则。"""
        ...


class StaticEndpointGuardRuleProvider:
    """从配置字符串或内存对象读取规则。"""

    def __init__(
        self, rules: str | Sequence[Mapping[str, object] | EndpointGuardRule] | None
    ):
        self._rules = parse_endpoint_guard_rules(rules)

    async def get_rules(self) -> Sequence[EndpointGuardRule]:
        return self._rules


class RedisEndpointGuardRuleProvider:
    """从 Redis 读取规则，并保留最近一次成功加载的规则。"""

    def __init__(
        self,
        *,
        cache: EndpointGuardCache,
        redis_key: str,
        cache_ttl_seconds: int,
        fail_open: bool,
    ):
        self._cache = cache
        self._redis_key = redis_key
        self._cache_ttl_seconds = max(cache_ttl_seconds, 0)
        self._fail_open = fail_open
        self._cached_rules: tuple[EndpointGuardRule, ...] = ()
        self._has_last_good = False
        self._last_loaded_at = 0.0

    async def get_rules(self) -> Sequence[EndpointGuardRule]:
        current = time.monotonic()
        if (
            self._has_last_good
            and self._cache_ttl_seconds > 0
            and current - self._last_loaded_at < self._cache_ttl_seconds
        ):
            return self._cached_rules

        try:
            payload = await self._cache.get_rules_payload(self._redis_key)
            rules = parse_endpoint_guard_rules(payload)
            self._cached_rules = rules
            self._has_last_good = True
            self._last_loaded_at = current
            return rules
        except Exception as exc:
            logger.error(
                f"Failed to load endpoint guard rules from Redis, key={self._redis_key}, error={exc!r}"
            )
            if self._has_last_good:
                return self._cached_rules
            if self._fail_open:
                return ()
            raise EndpointGuardProviderError(
                "endpoint guard rule source unavailable"
            ) from exc


class CompositeEndpointGuardRuleProvider:
    """合并多个规则源。"""

    def __init__(self, providers: Sequence[EndpointGuardRuleProvider]):
        self._providers = tuple(providers)

    async def get_rules(self) -> Sequence[EndpointGuardRule]:
        rules: list[EndpointGuardRule] = []
        for provider in self._providers:
            rules.extend(await provider.get_rules())
        return tuple(rules)


class _EndpointGuardContext(BaseMiddlewareContext):
    """Endpoint guard 请求上下文。"""

    def should_skip_guard(self) -> bool:
        """跳过非业务请求。"""
        return self.method.upper() == "OPTIONS"


class ASGIEndpointGuardMiddleware:
    """根据配置化规则拒绝访问指定 endpoint。"""

    def __init__(
        self,
        app: ASGIApp,
        *,
        enabled: bool | None = None,
        provider: EndpointGuardRuleProvider | None = None,
    ):
        self.app = app
        self._enabled = (
            bool(settings.ENDPOINT_GUARD_ENABLED) if enabled is None else enabled
        )
        self._provider = provider or new_endpoint_guard_rule_provider()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._enabled:
            await self.app(scope, receive, send)
            return

        guard_ctx = _EndpointGuardContext(scope)
        if guard_ctx.should_skip_guard():
            await self.app(scope, receive, send)
            return

        async with span_context(_SPAN_NAME):
            matched_rule = await self._find_matched_rule(guard_ctx)
            if matched_rule is not None:
                self._reject_request(guard_ctx, matched_rule)

        await self.app(scope, receive, send)

    async def _find_matched_rule(
        self, guard_ctx: _EndpointGuardContext
    ) -> EndpointGuardRule | None:
        try:
            rules = await self._provider.get_rules()
        except EndpointGuardProviderError as exc:
            raise AppException(
                errors.ServiceUnavailable,
                message="endpoint guard rule source unavailable",
            ) from exc

        now = datetime.now(UTC)
        for rule in rules:
            if rule.is_active(now=now) and _rule_matches(
                rule, method=guard_ctx.method, path=guard_ctx.path
            ):
                return rule
        return None

    @staticmethod
    def _reject_request(
        guard_ctx: _EndpointGuardContext, rule: EndpointGuardRule
    ) -> None:
        logger.warning(
            f"Endpoint blocked, method={guard_ctx.method}, path={guard_ctx.path}, "
            f"rule_id={rule.id}, reason={rule.reason}"
        )
        raise AppException(_error_for_rule(rule), message=_message_for_rule(rule))


def parse_endpoint_guard_rules(
    rules: str | Sequence[Mapping[str, object] | EndpointGuardRule] | None,
) -> tuple[EndpointGuardRule, ...]:
    """解析 endpoint guard 规则。"""
    if rules is None or rules == "":
        return ()

    raw_rules: object
    raw_rules = orjson_loads(rules) if isinstance(rules, str) else rules

    if not isinstance(raw_rules, Sequence) or isinstance(raw_rules, str | bytes):
        raise ValueError("endpoint guard rules must be a list")

    parsed_rules: list[EndpointGuardRule] = []
    for raw_rule in raw_rules:
        if isinstance(raw_rule, EndpointGuardRule):
            parsed_rules.append(raw_rule)
            continue
        if not isinstance(raw_rule, Mapping):
            raise ValueError("endpoint guard rule must be an object")
        try:
            parsed_rules.append(EndpointGuardRule.model_validate(raw_rule))
        except ValidationError as exc:
            raise ValueError(f"invalid endpoint guard rule: {exc}") from exc
    return tuple(parsed_rules)


def new_endpoint_guard_rule_provider() -> EndpointGuardRuleProvider:
    """按配置创建 endpoint guard 规则提供方。"""
    source = EndpointGuardSource(settings.ENDPOINT_GUARD_SOURCE)
    providers: list[EndpointGuardRuleProvider] = []

    if source in {EndpointGuardSource.SETTINGS, EndpointGuardSource.SETTINGS_AND_REDIS}:
        providers.append(StaticEndpointGuardRuleProvider(settings.ENDPOINT_GUARD_RULES))

    if source in {EndpointGuardSource.REDIS, EndpointGuardSource.SETTINGS_AND_REDIS}:
        providers.append(
            RedisEndpointGuardRuleProvider(
                cache=new_endpoint_guard_cache(),
                redis_key=settings.ENDPOINT_GUARD_REDIS_KEY,
                cache_ttl_seconds=settings.ENDPOINT_GUARD_CACHE_TTL_SECONDS,
                fail_open=settings.ENDPOINT_GUARD_FAIL_OPEN,
            )
        )

    if not providers:
        return StaticEndpointGuardRuleProvider(_DEFAULT_RULES_PAYLOAD)
    if len(providers) == 1:
        return providers[0]
    return CompositeEndpointGuardRuleProvider(providers)


def _rule_matches(rule: EndpointGuardRule, *, method: str, path: str) -> bool:
    if not _method_matches(rule.methods, method=method):
        return False

    if rule.match_type == EndpointGuardMatchType.EXACT:
        return path == rule.path
    if rule.match_type == EndpointGuardMatchType.PREFIX:
        return _prefix_matches(rule.path, path=path)
    if rule.match_type == EndpointGuardMatchType.GLOB:
        return fnmatch.fnmatchcase(path, rule.path)
    if rule.match_type == EndpointGuardMatchType.TEMPLATE:
        return _template_matches(rule.path, path=path)

    return False


def _method_matches(rule_methods: Sequence[str], *, method: str) -> bool:
    request_method = method.upper()
    if _ANY_METHOD in rule_methods:
        return True
    if request_method in rule_methods:
        return True
    return request_method == "HEAD" and "GET" in rule_methods


def _prefix_matches(rule_path: str, *, path: str) -> bool:
    if path == rule_path:
        return True
    if rule_path.endswith("/"):
        return path.startswith(rule_path)
    return path.startswith(f"{rule_path}/")


def _template_matches(rule_path: str, *, path: str) -> bool:
    rule_segments = _split_path(rule_path)
    request_segments = _split_path(path)
    if len(rule_segments) != len(request_segments):
        return False
    for rule_segment, request_segment in zip(
        rule_segments, request_segments, strict=True
    ):
        if rule_segment.startswith("{") and rule_segment.endswith("}"):
            if request_segment == "":
                return False
            continue
        if rule_segment != request_segment:
            return False
    return True


def _split_path(path: str) -> list[str]:
    return [segment for segment in path.strip("/").split("/") if segment]


def _error_for_rule(rule: EndpointGuardRule):
    if rule.error == EndpointGuardError.FORBIDDEN:
        return errors.Forbidden
    if rule.error == EndpointGuardError.UNAVAILABLE:
        return errors.ServiceUnavailable
    if rule.error == EndpointGuardError.NOT_FOUND:
        return errors.NotFound
    return errors.EndpointDisabled


def _message_for_rule(rule: EndpointGuardRule) -> str:
    if rule.message:
        return rule.message
    if rule.error == EndpointGuardError.UNAVAILABLE:
        return "endpoint is temporarily unavailable"
    if rule.error == EndpointGuardError.NOT_FOUND:
        return "endpoint not found"
    if rule.error == EndpointGuardError.FORBIDDEN:
        return "endpoint access forbidden"
    return "endpoint is disabled"
