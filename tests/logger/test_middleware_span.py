import importlib
import json
import sys
import types
from collections.abc import AsyncGenerator, Generator
from contextvars import ContextVar
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from loguru import logger as loguru_logger

from internal.core import errors
import pkg.logger as logger_module
from pkg.logger import LogFormat, init_logger
from pkg.logger.span import configure_span_logger
from pkg import request_context as context


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
        if not isinstance(trace_id, str) or trace_id in {"", "-", "unknown"}:
            raise LookupError(f"trace_id is invalid or not set, current value: {trace_id!r}")
        return trace_id


def _build_app(*, auth_middleware: type, record_middleware: type) -> FastAPI:
    app = FastAPI()
    app.add_middleware(auth_middleware)
    app.add_middleware(record_middleware)

    @app.get("/v1/public/ping")
    async def public_ping() -> dict[str, bool]:
        loguru_logger.info("handler.public")
        return {"ok": True}

    @app.get("/v1/internal/ping")
    async def internal_ping() -> dict[str, bool]:
        loguru_logger.info("handler.internal")
        return {"ok": True}

    @app.get("/secure")
    async def secure_ping() -> dict[str, bool]:
        loguru_logger.info("handler.secure")
        return {"ok": True}

    return app


def _default_log_file(base_log_dir: Path) -> Path:
    files = list(base_log_dir.glob("*.log"))
    assert len(files) == 1
    return files[0]


def _read_json_records(file_path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line))
    return records


def _find_record(records: list[dict[str, object]], message: str) -> dict[str, object]:
    for record in records:
        if record.get("message") == message:
            return record
    raise AssertionError(f"record not found for message={message}")


def _find_record_prefix(records: list[dict[str, object]], prefix: str) -> dict[str, object]:
    for record in records:
        message = record.get("message")
        if isinstance(message, str) and message.startswith(prefix):
            return record
    raise AssertionError(f"record not found for prefix={prefix}")


def _find_span_record(records: list[dict[str, object]], message: str, span_name: str) -> dict[str, object]:
    for record in records:
        if record.get("message") == message and record.get("span_name") == span_name:
            return record
    raise AssertionError(f"span record not found for message={message}, span_name={span_name}")


@pytest.fixture
def configured_logger(tmp_path: Path) -> Path:
    base_log_dir = tmp_path / "logs"
    init_logger(
        base_log_dir=base_log_dir,
        log_format=LogFormat.JSON,
        enqueue=False,
        write_to_console=False,
    )
    yield base_log_dir
    loguru_logger.remove()
    logger_module._logger = None
    logger_module._logger_manager = None
    configure_span_logger(None)


@pytest.fixture
def patched_request_context(monkeypatch: pytest.MonkeyPatch) -> _ContextStore:
    store = _ContextStore()
    monkeypatch.setattr(context, "init", store.init)
    monkeypatch.setattr(context, "clear", store.clear)
    monkeypatch.setattr(context, "set_val", store.set_val)
    monkeypatch.setattr(context, "get_trace_id", store.get_trace_id)
    return store


@pytest.fixture
def middleware_modules(monkeypatch: pytest.MonkeyPatch) -> Generator[tuple[ModuleType, ModuleType], None, None]:
    fake_auth_service = types.ModuleType("internal.services.auth")
    module_names = (
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

    auth_module = importlib.import_module("internal.middlewares.auth")
    recorder_module = importlib.import_module("internal.middlewares.recorder")
    yield auth_module, recorder_module

    for module_name, previous_module in previous_modules.items():
        if previous_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module


@pytest_asyncio.fixture
async def middleware_client(middleware_modules: tuple[ModuleType, ModuleType]) -> AsyncGenerator[AsyncClient, None]:
    auth_module, recorder_module = middleware_modules
    async with AsyncClient(
        transport=ASGITransport(
            app=_build_app(
                auth_middleware=auth_module.ASGIAuthMiddleware,
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
            {"X-Trace-ID": "trace-public"},
            "handler.public",
            "middleware.auth.whitelist",
        ),
        (
            "/v1/internal/ping",
            {
                "X-Trace-ID": "trace-internal",
                "X-Signature": "sig",
                "X-Timestamp": "1710000000",
                "X-Nonce": "nonce",
            },
            "handler.internal",
            "middleware.auth.internal",
        ),
        (
            "/secure",
            {"X-Trace-ID": "trace-token", "Authorization": "Bearer token-123"},
            "handler.secure",
            "middleware.auth.token",
        ),
    ],
)
async def test_middlewares_create_request_root_and_auth_child_spans(
    configured_logger: Path,
    patched_request_context: _ContextStore,
    middleware_modules: tuple[ModuleType, ModuleType],
    middleware_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    headers: dict[str, str],
    handler_message: str,
    auth_span_name: str,
) -> None:
    auth_module, _ = middleware_modules

    if auth_span_name == "middleware.auth.internal":
        monkeypatch.setattr(auth_module, "signature_auth_handler", SimpleNamespace(verify=MagicMock(return_value=True)))
    elif auth_span_name == "middleware.auth.token":
        fake_svc = AsyncMock()
        fake_svc.verify_token = AsyncMock(return_value={"id": 123})
        monkeypatch.setattr(auth_module, "new_auth_service", lambda: fake_svc)

    response = await middleware_client.get(path, headers=headers)

    assert response.status_code == 200
    assert response.headers["X-Trace-ID"] == headers["X-Trace-ID"]

    loguru_logger.complete()
    records = _read_json_records(_default_log_file(configured_logger))

    root_start = _find_span_record(records, "span.start", "middleware.request")
    auth_start = _find_span_record(records, "span.start", auth_span_name)
    handler_record = _find_record(records, handler_message)
    access_record = _find_record_prefix(records, "access log,")
    response_record = _find_record_prefix(records, "response log,")

    assert root_start["parent_span_seq"] is None
    assert access_record["span_name"] == "middleware.request"
    assert response_record["span_name"] == "middleware.request"

    assert auth_start["parent_span_seq"] == root_start["span_seq"]
    assert handler_record["span_seq"] == root_start["span_seq"]
    assert handler_record["parent_span_seq"] is None
    assert handler_record["span_name"] == "middleware.request"
    assert handler_record["span_path"] == root_start["span_path"]


@pytest.mark.asyncio
async def test_missing_token_logs_root_and_auth_span_errors(
    configured_logger: Path,
    patched_request_context: _ContextStore,
    middleware_client: AsyncClient,
) -> None:
    response = await middleware_client.get("/secure", headers={"X-Trace-ID": "trace-missing"})

    assert response.status_code == 200
    assert response.headers["X-Trace-ID"] == "trace-missing"
    assert response.json()["code"] == errors.Unauthorized.code

    loguru_logger.complete()
    records = _read_json_records(_default_log_file(configured_logger))

    root_error = _find_span_record(records, "span.error", "middleware.request")
    auth_error = _find_span_record(records, "span.error", "middleware.auth.token")
    exception_record = _find_record_prefix(records, "Business exception,")

    assert root_error["parent_span_seq"] is None
    assert auth_error["parent_span_seq"] == root_error["span_seq"]
    assert exception_record["span_name"] == "middleware.request"
