import importlib
import json
import sys
import types
from collections.abc import AsyncGenerator, Callable, Generator
from contextvars import ContextVar
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import anyio
import pytest
import pytest_asyncio
from fastapi import BackgroundTasks, Depends, FastAPI
from httpx import ASGITransport, AsyncClient, Response
from loguru import logger as loguru_logger
from opentelemetry import _logs, propagate, trace
from opentelemetry import metrics as otel_metrics
from opentelemetry.context import Context
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanContext, SpanKind, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

import internal.app as app_module
import pkg.logger as logger_module
import pkg.tracing.otel as otel_runtime
from internal.core import errors
from pkg import request_context as context
from pkg.logger import LogFormat, init_logger
from pkg.tracing import (
    RequestContextIdGenerator,
    init_tracing,
    shutdown_tracing,
    span_context,
)

_PUBLIC_TRACE_ID = "019da8cd058b76ed8a4a52141c1c6b38"
_INTERNAL_TRACE_ID = "019da8cd058b76ed8a4a52141c1c6b39"
_TOKEN_TRACE_ID = "019da8cd058b76ed8a4a52141c1c6b3a"
_MISSING_TOKEN_TRACE_ID = "019da8cd058b76ed8a4a52141c1c6b3b"
_TEST_USER_ID = UUID("00000000-0000-7000-8000-000000000123")
_PARENT_SPAN_ID = "b7ad6b7169203331"


def _traceparent(trace_id: str) -> str:
    return f"00-{trace_id}-{_PARENT_SPAN_ID}-01"


def _response_context(response: Response) -> SpanContext:
    extracted = TraceContextTextMapPropagator().extract(
        response.headers,
        context=Context(),
    )
    span_context = trace.get_current_span(extracted).get_span_context()
    assert span_context.is_valid
    assert "X-Trace-ID" not in response.headers
    return span_context


def _response_trace_id(response: Response) -> str:
    return f"{_response_context(response).trace_id:032x}"


class _ContextStore:
    def __init__(self) -> None:
        self._ctx_var: ContextVar[dict[str, object] | None] = ContextVar(
            "test_middleware_span_context",
            default=None,
        )

    def init(self, **kwargs) -> dict[str, object]:
        ctx = dict(kwargs)
        self._ctx_var.set(ctx)
        return ctx

    def clear(self) -> None:
        self._ctx_var.set(None)

    def set_val(self, key: object, value: object) -> None:
        normalized_key = getattr(key, "value", key)
        ctx = dict(self._ctx_var.get() or {})
        ctx[str(normalized_key)] = value
        self._ctx_var.set(ctx)

    def set_user_id(self, user_id: UUID) -> None:
        self.set_val(context.ContextKey.USER_ID, user_id)

    def get_trace_id(self) -> str:
        trace_id = (self._ctx_var.get() or {}).get(context.ContextKey.TRACE_ID.value)
        if not isinstance(trace_id, str) or not trace_id:
            raise LookupError(f"trace_id is invalid or not set: {trace_id!r}")
        return trace_id


def _build_app(
    *,
    internal_auth_dependency: Callable[..., object],
    monkeypatch: pytest.MonkeyPatch,
    record_middleware: type,
    token_auth_dependency: Callable[..., object],
    tracing: bool = True,
    operation_spans: bool = True,
    metrics: bool = False,
    logs: bool = False,
) -> FastAPI:
    monkeypatch.setattr(
        app_module,
        "settings",
        SimpleNamespace(
            debug=True,
            otel=SimpleNamespace(
                tracing_enabled=tracing,
                operation_spans_enabled=operation_spans,
                metrics_enabled=metrics,
                logs_enabled=logs,
                auto_configure=False,
            ),
            cors=SimpleNamespace(origins=[]),
        ),
    )
    monkeypatch.setattr(app_module, "register_router", lambda app: None)
    monkeypatch.setattr(
        app_module,
        "register_middleware",
        lambda app: app.add_middleware(
            record_middleware,
            tracing_enabled=tracing,
            logs_enabled=logs,
        ),
    )
    app = app_module.create_app()

    @app.get("/v1/public/ping")
    async def public_ping() -> dict[str, bool]:
        await anyio.sleep(0)
        loguru_logger.info("handler.public")
        return {"ok": True}

    @app.get(
        "/v1/internal/ping",
        dependencies=[Depends(internal_auth_dependency)],
    )
    async def internal_ping() -> dict[str, bool]:
        loguru_logger.info("handler.internal")
        return {"ok": True}

    @app.get(
        "/secure",
        dependencies=[Depends(token_auth_dependency)],
    )
    async def secure_ping() -> dict[str, bool]:
        loguru_logger.info("handler.secure")
        return {"ok": True}

    @app.get("/background")
    async def background_ping(background_tasks: BackgroundTasks) -> dict[str, bool]:
        async def run_job() -> None:
            async with span_context("business.background"):
                loguru_logger.info("handler.background")

        background_tasks.add_task(run_job)
        return {"ok": True}

    @app.get("/propagate")
    async def propagated_headers() -> dict[str, str]:
        headers: dict[str, str] = {}
        propagate.inject(headers)
        return headers

    @app.get("/cancel")
    async def cancelled_request() -> None:
        raise anyio.get_cancelled_exc_class()()

    @app.get("/validated")
    async def validated_request(value: int) -> dict[str, int]:
        return {"value": value}

    return app


def _read_json_records(base_log_dir: Path) -> list[dict[str, object]]:
    files = list(base_log_dir.glob("*.log"))
    assert len(files) == 1
    return [
        json.loads(line)
        for line in files[0].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _find_record(records: list[dict[str, object]], message: str) -> dict[str, object]:
    for record in records:
        if record.get("message") == message:
            return record
    raise AssertionError(f"record not found for message={message}")


def _find_record_prefix(
    records: list[dict[str, object]],
    prefix: str,
) -> dict[str, object]:
    for record in records:
        message = record.get("message")
        if isinstance(message, str) and message.startswith(prefix):
            return record
    raise AssertionError(f"record not found for prefix={prefix}")


@pytest.fixture
def configured_logger(tmp_path: Path) -> Path:
    previous_logger = logger_module._logger
    previous_manager = logger_module._logger_manager
    base_log_dir = tmp_path / "logs"
    init_logger(
        base_log_dir=base_log_dir,
        log_format=LogFormat.JSON,
        enqueue=False,
        write_to_file=True,
        write_to_console=False,
    )
    yield base_log_dir
    loguru_logger.remove()
    logger_module._logger = previous_logger
    logger_module._logger_manager = previous_manager


@pytest.fixture
def patched_request_context(monkeypatch: pytest.MonkeyPatch) -> _ContextStore:
    store = _ContextStore()
    monkeypatch.setattr(context, "init", store.init)
    monkeypatch.setattr(context, "clear", store.clear)
    monkeypatch.setattr(context, "set_val", store.set_val)
    monkeypatch.setattr(context, "set_user_id", store.set_user_id)
    monkeypatch.setattr(context, "get_trace_id", store.get_trace_id)
    monkeypatch.setattr(otel_runtime, "request_context", context)
    return store


@pytest.fixture
def tracing_runtime(
    patched_request_context: _ContextStore,
    monkeypatch: pytest.MonkeyPatch,
) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(id_generator=RequestContextIdGenerator())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    init_tracing(
        enabled=True,
        service_name="test-agent-backend",
        tracer_provider=provider,
    )
    # 使用独立 provider 验证真实 FastAPI 埋点，不占用进程的全局一次性注册。
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    yield exporter
    shutdown_tracing()
    provider.shutdown()


@pytest.fixture
def middleware_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[ModuleType, ModuleType], None, None]:
    fake_auth_service = types.ModuleType("internal.services.auth")
    module_names = (
        "internal.dependencies.auth",
        "internal.middlewares",
        "internal.middlewares.recorder",
    )
    previous_modules = {name: sys.modules.get(name) for name in module_names}

    class FakeAuthService:
        async def verify_token(self, _token: str) -> dict[str, object]:
            return {"id": _TEST_USER_ID.hex}

    fake_auth_service.AuthService = FakeAuthService
    fake_auth_service.new_auth_service = lambda: FakeAuthService()
    monkeypatch.setitem(sys.modules, "internal.services.auth", fake_auth_service)
    for module_name in module_names:
        sys.modules.pop(module_name, None)

    internal_auth_module = importlib.import_module("internal.dependencies.auth")
    recorder_module = importlib.import_module("internal.middlewares.recorder")
    yield internal_auth_module, recorder_module

    for module_name, previous_module in previous_modules.items():
        if previous_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module


@pytest_asyncio.fixture
async def middleware_client(
    middleware_modules: tuple[ModuleType, ModuleType],
    tracing_runtime: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[AsyncClient, None]:
    internal_auth_module, recorder_module = middleware_modules
    async with AsyncClient(
        transport=ASGITransport(
            app=_build_app(
                internal_auth_dependency=internal_auth_module.require_internal_signature,
                monkeypatch=monkeypatch,
                record_middleware=recorder_module.ASGIRecordMiddleware,
                token_auth_dependency=internal_auth_module.require_authenticated_user,
            )
        ),
        base_url="http://testserver",
    ) as client:
        yield client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "headers", "handler_message", "auth_span_name"),
    [
        (
            "/v1/public/ping",
            {"traceparent": _traceparent(_PUBLIC_TRACE_ID)},
            "handler.public",
            None,
        ),
        (
            "/v1/internal/ping",
            {
                "traceparent": _traceparent(_INTERNAL_TRACE_ID),
                "X-Signature": "sig",
                "X-Timestamp": "1710000000",
                "X-Nonce": "nonce",
            },
            "handler.internal",
            "middleware.auth.internal",
        ),
        (
            "/secure",
            {
                "traceparent": _traceparent(_TOKEN_TRACE_ID),
                "Authorization": "Bearer token-123",
            },
            "handler.secure",
            "middleware.auth.token",
        ),
    ],
)
async def test_request_and_route_auth_create_expected_spans(
    configured_logger: Path,
    middleware_modules: tuple[ModuleType, ModuleType],
    middleware_client: AsyncClient,
    tracing_runtime: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    headers: dict[str, str],
    handler_message: str,
    auth_span_name: str | None,
) -> None:
    internal_auth_module, _ = middleware_modules
    if auth_span_name == "middleware.auth.internal":
        monkeypatch.setattr(
            internal_auth_module,
            "signature_auth_handler",
            SimpleNamespace(verify=MagicMock(return_value=True)),
        )

    response = await middleware_client.get(path, headers=headers)
    expected_trace_id = headers["traceparent"].split("-")[1]
    assert response.status_code == 200
    assert _response_trace_id(response) == expected_trace_id

    finished = {span.name: span for span in tracing_runtime.get_finished_spans()}
    root = finished[f"GET {path}"]
    assert root.kind is SpanKind.SERVER
    assert root.parent.is_remote is True
    assert root.parent.span_id == int(_PARENT_SPAN_ID, 16)
    assert f"{root.context.trace_id:032x}" == expected_trace_id
    assert root.instrumentation_scope.name == "fastapi"
    assert sum(span.kind is SpanKind.SERVER for span in finished.values()) == 1
    assert "middleware.request" not in finished
    for name in ("fastapi.dependencies", "fastapi.endpoint", "fastapi.serialization"):
        assert finished[name].parent.span_id == root.context.span_id
    if auth_span_name is None:
        assert all(not name.startswith("middleware.auth.") for name in finished)
    else:
        auth = finished[auth_span_name]
        assert auth.parent is not None
        assert auth.parent.span_id == finished["fastapi.dependencies"].context.span_id

    loguru_logger.complete()
    records = _read_json_records(configured_logger)
    handler_record = _find_record(records, handler_message)
    access_record = _find_record_prefix(records, "access log,")
    response_record = _find_record_prefix(records, "response log,")
    assert handler_record["trace_id"] == expected_trace_id
    assert "span_id" not in handler_record
    assert "span_id" not in access_record
    assert "span_id" not in response_record


@pytest.mark.asyncio
async def test_missing_token_marks_auth_and_request_spans_as_error(
    configured_logger: Path,
    middleware_client: AsyncClient,
    tracing_runtime: InMemorySpanExporter,
) -> None:
    response = await middleware_client.get(
        "/secure",
        headers={"traceparent": _traceparent(_MISSING_TOKEN_TRACE_ID)},
    )

    assert response.status_code == 200
    assert _response_trace_id(response) == _MISSING_TOKEN_TRACE_ID
    assert response.json()["code"] == errors.Unauthorized.code

    finished = {span.name: span for span in tracing_runtime.get_finished_spans()}
    root = finished["GET /secure"]
    auth = finished["middleware.auth.token"]
    assert root.status.status_code is StatusCode.ERROR
    assert auth.status.status_code is StatusCode.ERROR
    assert [event.name for event in root.events].count("exception") == 1
    assert [event.name for event in auth.events].count("exception") == 1

    loguru_logger.complete()
    records = _read_json_records(configured_logger)
    exception_record = _find_record_prefix(records, "Business exception,")
    assert exception_record["trace_id"] == _MISSING_TOKEN_TRACE_ID
    assert "span_id" not in exception_record


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"traceparent": "invalid"},
        {"traceparent": "00-00000000000000000000000000000000-b7ad6b7169203331-01"},
    ],
)
async def test_missing_or_invalid_traceparent_starts_a_new_trace(
    configured_logger: Path,
    middleware_client: AsyncClient,
    headers: dict[str, str],
) -> None:
    response = await middleware_client.get(
        "/v1/public/ping",
        headers=headers,
    )

    returned = _response_context(response)
    assert returned.trace_id != 0
    assert returned.span_id != 0


@pytest.mark.asyncio
@pytest.mark.parametrize("sampled", [True, False])
async def test_traceparent_preserves_remote_sampling_and_trace_state(
    configured_logger: Path,
    middleware_client: AsyncClient,
    tracing_runtime: InMemorySpanExporter,
    patched_request_context: _ContextStore,
    sampled: bool,
) -> None:
    remote_trace_id = "0af7651916cd43dd8448eb211c80319c"
    remote_span_id = "b7ad6b7169203331"
    flags = "01" if sampled else "00"
    response = await middleware_client.get(
        "/v1/public/ping",
        headers={
            "traceparent": f"00-{remote_trace_id}-{remote_span_id}-{flags}",
            "tracestate": "vendor=test",
        },
    )

    assert response.status_code == 200
    assert _response_trace_id(response) == remote_trace_id
    assert _response_context(response).trace_flags.sampled is sampled
    finished = tracing_runtime.get_finished_spans()
    if sampled:
        roots = [span for span in finished if span.kind is SpanKind.SERVER]
        assert len(roots) == 1
        root = roots[0]
        assert root.parent.is_remote is True
        assert root.parent.span_id == int(remote_span_id, 16)
        assert root.parent.trace_state.get("vendor") == "test"
        assert all(
            span.context.trace_id == int(remote_trace_id, 16) for span in finished
        )
    else:
        assert finished == ()

    loguru_logger.complete()
    records = _read_json_records(configured_logger)
    assert _find_record(records, "handler.public")["trace_id"] == remote_trace_id
    with pytest.raises(LookupError):
        patched_request_context.get_trace_id()
    assert trace.get_current_span().get_span_context().is_valid is False


@pytest.mark.asyncio
async def test_x_trace_id_is_ignored_when_traceparent_is_invalid(
    configured_logger: Path,
    middleware_client: AsyncClient,
    tracing_runtime: InMemorySpanExporter,
) -> None:
    response = await middleware_client.get(
        "/v1/public/ping",
        headers={"traceparent": "invalid", "X-Trace-ID": _PUBLIC_TRACE_ID},
    )

    assert _response_trace_id(response) != _PUBLIC_TRACE_ID
    roots = [
        span
        for span in tracing_runtime.get_finished_spans()
        if span.kind is SpanKind.SERVER
    ]
    assert len(roots) == 1
    assert roots[0].parent is None
    assert f"{roots[0].context.trace_id:032x}" == _response_trace_id(response)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "expected_trace_id"),
    [
        ({}, None),
        ({"traceparent": _traceparent(_PUBLIC_TRACE_ID)}, _PUBLIC_TRACE_ID),
        (
            {
                "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
            },
            "0af7651916cd43dd8448eb211c80319c",
        ),
        ({"traceparent": "invalid", "X-Trace-ID": "invalid"}, None),
        ({"X-Trace-ID": _PUBLIC_TRACE_ID}, None),
    ],
)
async def test_request_correlation_works_without_telemetry(
    configured_logger: Path,
    middleware_modules: tuple[ModuleType, ModuleType],
    tracing_runtime: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    expected_trace_id: str | None,
) -> None:
    shutdown_tracing()
    init_tracing(enabled=False, service_name="test-agent-backend")
    auth_module, recorder_module = middleware_modules
    app = _build_app(
        internal_auth_dependency=auth_module.require_internal_signature,
        monkeypatch=monkeypatch,
        record_middleware=recorder_module.ASGIRecordMiddleware,
        token_auth_dependency=auth_module.require_authenticated_user,
        tracing=False,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/secure",
            headers={**headers, "Authorization": "Bearer token-123"},
        )

    assert response.status_code == 200
    returned_trace_id = _response_trace_id(response)
    if expected_trace_id is None:
        assert _response_context(response).trace_flags.sampled is False
        if "X-Trace-ID" in headers:
            assert returned_trace_id != headers["X-Trace-ID"]
    else:
        assert returned_trace_id == expected_trace_id
    assert tracing_runtime.get_finished_spans() == ()
    loguru_logger.complete()
    records = _read_json_records(configured_logger)
    assert _find_record(records, "handler.secure")["trace_id"] == returned_trace_id


@pytest.mark.asyncio
async def test_disabled_request_does_not_reuse_or_modify_ambient_span(
    configured_logger: Path,
    middleware_modules: tuple[ModuleType, ModuleType],
    tracing_runtime: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shutdown_tracing()
    init_tracing(enabled=False, service_name="test-agent-backend")
    auth_module, recorder_module = middleware_modules
    app = _build_app(
        internal_auth_dependency=auth_module.require_internal_signature,
        monkeypatch=monkeypatch,
        record_middleware=recorder_module.ASGIRecordMiddleware,
        token_auth_dependency=auth_module.require_authenticated_user,
        tracing=False,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        with trace.get_tracer("caller").start_as_current_span("caller") as caller:
            response = await client.get(
                "/secure",
                headers={"traceparent": _traceparent(_PUBLIC_TRACE_ID)},
            )
            assert trace.get_current_span() is caller

    assert _response_trace_id(response) == _PUBLIC_TRACE_ID
    assert response.json()["code"] == errors.Unauthorized.code
    finished = tracing_runtime.get_finished_spans()
    assert len(finished) == 1
    assert finished[0].name == "caller"
    assert finished[0].status.status_code is StatusCode.UNSET
    assert finished[0].events == ()


@pytest.mark.asyncio
async def test_concurrent_requests_keep_independent_trace_ids(
    configured_logger: Path,
    middleware_client: AsyncClient,
    tracing_runtime: InMemorySpanExporter,
    patched_request_context: _ContextStore,
) -> None:
    trace_ids = {_PUBLIC_TRACE_ID, _TOKEN_TRACE_ID, _INTERNAL_TRACE_ID}
    returned_trace_ids: set[str] = set()

    async def request(trace_id: str) -> None:
        response = await middleware_client.get(
            "/v1/public/ping",
            headers={"traceparent": _traceparent(trace_id)},
        )
        returned_trace_ids.add(_response_trace_id(response))

    async with anyio.create_task_group() as task_group:
        for trace_id in trace_ids:
            task_group.start_soon(request, trace_id)

    assert returned_trace_ids == trace_ids
    roots = [
        span
        for span in tracing_runtime.get_finished_spans()
        if span.kind is SpanKind.SERVER
    ]
    assert len(roots) == 3
    assert {f"{span.context.trace_id:032x}" for span in roots} == trace_ids
    with pytest.raises(LookupError):
        patched_request_context.get_trace_id()


@pytest.mark.asyncio
async def test_background_tasks_keep_the_request_trace_after_response(
    configured_logger: Path,
    middleware_client: AsyncClient,
    tracing_runtime: InMemorySpanExporter,
) -> None:
    response = await middleware_client.get(
        "/background",
        headers={"traceparent": _traceparent(_PUBLIC_TRACE_ID)},
    )

    assert response.status_code == 200
    finished = {span.name: span for span in tracing_runtime.get_finished_spans()}
    root = finished["GET /background"]
    background = finished["fastapi.background_task"]
    business = finished["business.background"]
    assert background.parent.span_id == root.context.span_id
    assert business.parent.span_id == background.context.span_id
    assert business.context.trace_id == root.context.trace_id
    assert root.end_time <= background.end_time
    loguru_logger.complete()
    records = _read_json_records(configured_logger)
    assert _find_record(records, "handler.background")["trace_id"] == _PUBLIC_TRACE_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("tracing", [True, False])
@pytest.mark.parametrize("valid_parent", [True, False])
async def test_context_can_be_injected_independently_of_collection(
    configured_logger: Path,
    middleware_modules: tuple[ModuleType, ModuleType],
    tracing_runtime: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
    tracing: bool,
    valid_parent: bool,
) -> None:
    if not tracing:
        shutdown_tracing()
        init_tracing(enabled=False, service_name="test-agent-backend")
    auth_module, recorder_module = middleware_modules
    app = _build_app(
        internal_auth_dependency=auth_module.require_internal_signature,
        monkeypatch=monkeypatch,
        record_middleware=recorder_module.ASGIRecordMiddleware,
        token_auth_dependency=auth_module.require_authenticated_user,
        tracing=tracing,
    )
    incoming = _traceparent(_PUBLIC_TRACE_ID) if valid_parent else "invalid"
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/propagate",
            headers={"traceparent": incoming, "tracestate": "vendor=test"},
        )

    forwarded = TraceContextTextMapPropagator().extract(
        response.json(), context=Context()
    )
    forwarded_span_context = trace.get_current_span(forwarded).get_span_context()
    assert forwarded_span_context.is_valid
    assert forwarded_span_context.trace_id == _response_context(response).trace_id
    if valid_parent:
        assert f"{forwarded_span_context.trace_id:032x}" == _PUBLIC_TRACE_ID
        assert forwarded_span_context.trace_state.get("vendor") == "test"
        if not tracing:
            assert response.json()["traceparent"] == incoming
    else:
        assert forwarded_span_context.trace_state.get("vendor") is None
    if not tracing:
        assert tracing_runtime.get_finished_spans() == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("tracing", [True, False])
async def test_cancelled_requests_restore_caller_and_request_contexts(
    configured_logger: Path,
    middleware_modules: tuple[ModuleType, ModuleType],
    tracing_runtime: InMemorySpanExporter,
    patched_request_context: _ContextStore,
    monkeypatch: pytest.MonkeyPatch,
    tracing: bool,
) -> None:
    if not tracing:
        shutdown_tracing()
        init_tracing(enabled=False, service_name="test-agent-backend")
    auth_module, recorder_module = middleware_modules
    app = _build_app(
        internal_auth_dependency=auth_module.require_internal_signature,
        monkeypatch=monkeypatch,
        record_middleware=recorder_module.ASGIRecordMiddleware,
        token_auth_dependency=auth_module.require_authenticated_user,
        tracing=tracing,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        with trace.get_tracer("caller").start_as_current_span("caller") as caller:
            with pytest.raises(anyio.get_cancelled_exc_class()):
                await client.get(
                    "/cancel",
                    headers={"traceparent": _traceparent(_PUBLIC_TRACE_ID)},
                )
            assert trace.get_current_span() is caller
            with pytest.raises(LookupError):
                patched_request_context.get_trace_id()

    roots = [
        span
        for span in tracing_runtime.get_finished_spans()
        if span.kind is SpanKind.SERVER
    ]
    assert len(roots) == (1 if tracing else 0)
    if tracing:
        assert roots[0].status.status_code is StatusCode.ERROR


@pytest.mark.asyncio
async def test_operation_spans_can_be_disabled_without_disabling_business_spans(
    configured_logger: Path,
    middleware_modules: tuple[ModuleType, ModuleType],
    tracing_runtime: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_module, recorder_module = middleware_modules
    app = _build_app(
        internal_auth_dependency=auth_module.require_internal_signature,
        monkeypatch=monkeypatch,
        record_middleware=recorder_module.ASGIRecordMiddleware,
        token_auth_dependency=auth_module.require_authenticated_user,
        operation_spans=False,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/secure",
            headers={
                "traceparent": _traceparent(_PUBLIC_TRACE_ID),
                "Authorization": "Bearer token-123",
            },
        )

    assert response.status_code == 200
    assert _response_trace_id(response) == _PUBLIC_TRACE_ID
    finished = {span.name: span for span in tracing_runtime.get_finished_spans()}
    assert set(finished) == {"GET /secure", "middleware.auth.token"}
    assert (
        finished["middleware.auth.token"].parent.span_id
        == finished["GET /secure"].context.span_id
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("metrics_enabled", [True, False])
@pytest.mark.parametrize("logs_enabled", [True, False])
@pytest.mark.parametrize("with_parent", [True, False])
async def test_metrics_and_logs_are_independent_of_tracing_and_preserve_correlation(
    configured_logger: Path,
    middleware_modules: tuple[ModuleType, ModuleType],
    tracing_runtime: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
    metrics_enabled: bool,
    logs_enabled: bool,
    with_parent: bool,
) -> None:
    shutdown_tracing()
    init_tracing(enabled=False, service_name="test-agent-backend")
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader], shutdown_on_exit=False)
    log_exporter = InMemoryLogRecordExporter()
    logger_provider = LoggerProvider(shutdown_on_exit=False)
    logger_provider.add_log_record_processor(SimpleLogRecordProcessor(log_exporter))
    monkeypatch.setattr(otel_metrics, "get_meter_provider", lambda: meter_provider)
    monkeypatch.setattr(_logs, "get_logger_provider", lambda: logger_provider)
    original_get_logger = _logs.get_logger

    def get_test_logger(
        name: str,
        version: str | None = None,
        provider: LoggerProvider | None = None,
        *,
        schema_url: str | None = None,
    ) -> _logs.Logger:
        # 显式注入测试 Provider，避免占用 SDK 的进程级一次性注册。
        return original_get_logger(
            name,
            version,
            logger_provider=provider or logger_provider,
            schema_url=schema_url,
        )

    monkeypatch.setattr(_logs, "get_logger", get_test_logger)
    auth_module, recorder_module = middleware_modules
    app = _build_app(
        internal_auth_dependency=auth_module.require_internal_signature,
        monkeypatch=monkeypatch,
        record_middleware=recorder_module.ASGIRecordMiddleware,
        token_auth_dependency=auth_module.require_authenticated_user,
        tracing=False,
        metrics=metrics_enabled,
        logs=logs_enabled,
    )
    try:
        headers = {"traceparent": _traceparent(_PUBLIC_TRACE_ID)} if with_parent else {}
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            with trace.get_tracer("caller").start_as_current_span("caller") as caller:
                response = await client.get(
                    "/validated",
                    params={"value": "invalid"},
                    headers=headers,
                )
                assert trace.get_current_span() is caller
        metric_data = reader.get_metrics_data()
        logs = log_exporter.get_finished_logs()
    finally:
        meter_provider.shutdown()
        logger_provider.shutdown()

    assert response.json()["code"] == errors.BadRequest.code
    response_trace_id = _response_trace_id(response)
    if with_parent:
        assert response_trace_id == _PUBLIC_TRACE_ID
    finished = tracing_runtime.get_finished_spans()
    assert len(finished) == 1
    assert finished[0].name == "caller"
    assert finished[0].status.status_code is StatusCode.UNSET
    assert response_trace_id != f"{finished[0].context.trace_id:032x}"

    metric_names = set()
    if metric_data is not None:
        for resource in metric_data.resource_metrics:
            for scope in resource.scope_metrics:
                metric_names.update(metric.name for metric in scope.metrics)
    assert ("http.server.request.duration" in metric_names) is metrics_enabled
    assert bool(logs) is logs_enabled
    if logs_enabled:
        record = logs[0].log_record
        assert record.event_name == "fastapi.validation.failed"
        assert f"{record.trace_id:032x}" == response_trace_id


@pytest.mark.asyncio
@pytest.mark.parametrize("logs_enabled", [True, False])
async def test_unhandled_exception_emits_otel_log_when_enabled(
    configured_logger: Path,
    middleware_modules: tuple[ModuleType, ModuleType],
    tracing_runtime: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
    logs_enabled: bool,
) -> None:
    shutdown_tracing()
    init_tracing(enabled=False, service_name="test-agent-backend")
    log_exporter = InMemoryLogRecordExporter()
    logger_provider = LoggerProvider(shutdown_on_exit=False)
    logger_provider.add_log_record_processor(SimpleLogRecordProcessor(log_exporter))
    monkeypatch.setattr(_logs, "get_logger_provider", lambda: logger_provider)

    auth_module, recorder_module = middleware_modules
    app = _build_app(
        internal_auth_dependency=auth_module.require_internal_signature,
        monkeypatch=monkeypatch,
        record_middleware=recorder_module.ASGIRecordMiddleware,
        token_auth_dependency=auth_module.require_authenticated_user,
        tracing=False,
        logs=logs_enabled,
    )

    @app.get("/boom")
    async def boom() -> dict[str, bool]:
        raise RuntimeError("kaboom")

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.get(
                "/boom",
                headers={"traceparent": _traceparent(_PUBLIC_TRACE_ID)},
            )
        logs = log_exporter.get_finished_logs()
    finally:
        logger_provider.shutdown()

    assert response.status_code == 200
    assert response.json()["code"] == errors.InternalServerError.code
    assert tracing_runtime.get_finished_spans() == ()
    assert bool(logs) is logs_enabled
    if logs_enabled:
        record = logs[0].log_record
        assert record.event_name == "http.server.request.exception"
        assert f"{record.trace_id:032x}" == _PUBLIC_TRACE_ID
