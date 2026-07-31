import importlib
import json
import sys
import types
from collections.abc import AsyncGenerator, Callable, Generator
from contextvars import ContextVar
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from loguru import logger as loguru_logger
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

import pkg.logger as logger_module
import pkg.tracing.otel as otel_runtime
from internal.core import errors
from pkg import request_context as context
from pkg.logger import LogFormat, init_logger
from pkg.tracing import RequestContextIdGenerator, init_tracing, shutdown_tracing

_PUBLIC_TRACE_ID = "019da8cd058b76ed8a4a52141c1c6b38"
_INTERNAL_TRACE_ID = "019da8cd058b76ed8a4a52141c1c6b39"
_TOKEN_TRACE_ID = "019da8cd058b76ed8a4a52141c1c6b3a"
_MISSING_TOKEN_TRACE_ID = "019da8cd058b76ed8a4a52141c1c6b3b"


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

    def get_trace_id(self) -> str:
        trace_id = (self._ctx_var.get() or {}).get(context.ContextKey.TRACE_ID.value)
        if not isinstance(trace_id, str) or not trace_id:
            raise LookupError(f"trace_id is invalid or not set: {trace_id!r}")
        return trace_id


def _build_app(
    *,
    auth_middleware: type,
    internal_auth_dependency: Callable[..., object],
    record_middleware: type,
) -> FastAPI:
    app = FastAPI()
    app.add_middleware(auth_middleware)
    app.add_middleware(record_middleware)

    @app.get("/v1/public/ping")
    async def public_ping() -> dict[str, bool]:
        loguru_logger.info("handler.public")
        return {"ok": True}

    @app.get(
        "/v1/internal/ping",
        dependencies=[Depends(internal_auth_dependency)],
    )
    async def internal_ping() -> dict[str, bool]:
        loguru_logger.info("handler.internal")
        return {"ok": True}

    @app.get("/secure")
    async def secure_ping() -> dict[str, bool]:
        loguru_logger.info("handler.secure")
        return {"ok": True}

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
    monkeypatch.setattr(context, "get_trace_id", store.get_trace_id)
    monkeypatch.setattr(otel_runtime, "request_context", context)
    return store


@pytest.fixture
def tracing_runtime(
    patched_request_context: _ContextStore,
) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(id_generator=RequestContextIdGenerator())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    init_tracing(
        enabled=True,
        service_name="test-agent-backend",
        tracer_provider=provider,
    )
    yield exporter
    shutdown_tracing()
    provider.shutdown()


@pytest.fixture
def middleware_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[ModuleType, ModuleType, ModuleType], None, None]:
    fake_auth_service = types.ModuleType("internal.services.auth")
    module_names = (
        "internal.dependencies.auth",
        "internal.middlewares",
        "internal.middlewares.auth",
        "internal.middlewares.recorder",
    )
    previous_modules = {name: sys.modules.get(name) for name in module_names}

    class FakeAuthService:
        async def verify_token(self, _token: str) -> dict[str, object]:
            raise AssertionError("verify_token should be patched in this test")

    fake_auth_service.new_auth_service = lambda: FakeAuthService()
    monkeypatch.setitem(sys.modules, "internal.services.auth", fake_auth_service)
    for module_name in module_names:
        sys.modules.pop(module_name, None)

    internal_auth_module = importlib.import_module("internal.dependencies.auth")
    auth_module = importlib.import_module("internal.middlewares.auth")
    recorder_module = importlib.import_module("internal.middlewares.recorder")
    yield auth_module, internal_auth_module, recorder_module

    for module_name, previous_module in previous_modules.items():
        if previous_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module


@pytest_asyncio.fixture
async def middleware_client(
    middleware_modules: tuple[ModuleType, ModuleType, ModuleType],
    tracing_runtime: InMemorySpanExporter,
) -> AsyncGenerator[AsyncClient, None]:
    auth_module, internal_auth_module, recorder_module = middleware_modules
    async with AsyncClient(
        transport=ASGITransport(
            app=_build_app(
                auth_middleware=auth_module.ASGIAuthMiddleware,
                internal_auth_dependency=internal_auth_module.require_internal_signature,
                record_middleware=recorder_module.ASGIRecordMiddleware,
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
            {"X-Trace-ID": _PUBLIC_TRACE_ID},
            "handler.public",
            "middleware.auth.whitelist",
        ),
        (
            "/v1/internal/ping",
            {
                "X-Trace-ID": _INTERNAL_TRACE_ID,
                "X-Signature": "sig",
                "X-Timestamp": "1710000000",
                "X-Nonce": "nonce",
            },
            "handler.internal",
            "middleware.auth.internal",
        ),
        (
            "/secure",
            {"X-Trace-ID": _TOKEN_TRACE_ID, "Authorization": "Bearer token-123"},
            "handler.secure",
            "middleware.auth.token",
        ),
    ],
)
async def test_middlewares_create_real_request_and_auth_spans(
    configured_logger: Path,
    middleware_modules: tuple[ModuleType, ModuleType, ModuleType],
    middleware_client: AsyncClient,
    tracing_runtime: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    headers: dict[str, str],
    handler_message: str,
    auth_span_name: str,
) -> None:
    auth_module, internal_auth_module, _ = middleware_modules
    if auth_span_name == "middleware.auth.internal":
        monkeypatch.setattr(
            internal_auth_module,
            "signature_auth_handler",
            SimpleNamespace(verify=MagicMock(return_value=True)),
        )
    elif auth_span_name == "middleware.auth.token":
        fake_svc = AsyncMock()
        fake_svc.verify_token = AsyncMock(return_value={"id": 123})
        monkeypatch.setattr(auth_module, "new_auth_service", lambda: fake_svc)

    response = await middleware_client.get(path, headers=headers)
    expected_trace_id = headers["X-Trace-ID"]
    assert response.status_code == 200
    assert response.headers["X-Trace-ID"] == expected_trace_id

    finished = {span.name: span for span in tracing_runtime.get_finished_spans()}
    root = finished["middleware.request"]
    auth = finished[auth_span_name]
    assert root.kind is SpanKind.SERVER
    assert root.parent is None
    assert f"{root.context.trace_id:032x}" == expected_trace_id
    assert auth.parent is not None
    assert auth.parent.span_id == root.context.span_id

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
        headers={"X-Trace-ID": _MISSING_TOKEN_TRACE_ID},
    )

    assert response.status_code == 200
    assert response.headers["X-Trace-ID"] == _MISSING_TOKEN_TRACE_ID
    assert response.json()["code"] == errors.Unauthorized.code

    finished = {span.name: span for span in tracing_runtime.get_finished_spans()}
    root = finished["middleware.request"]
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
async def test_invalid_x_trace_id_is_replaced_with_uuid7(
    configured_logger: Path,
    middleware_client: AsyncClient,
) -> None:
    response = await middleware_client.get(
        "/v1/public/ping",
        headers={"X-Trace-ID": "not-a-w3c-trace-id"},
    )

    returned_trace_id = response.headers["X-Trace-ID"]
    assert returned_trace_id != "not-a-w3c-trace-id"
    assert len(returned_trace_id) == 32
    assert int(returned_trace_id, 16) != 0
