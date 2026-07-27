import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from loguru import logger as loguru_logger
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

import pkg.tracing.otel as otel_runtime
from pkg.concurrency import anyio_gather
from pkg.logger import LogFormat, LoggerHandler
from pkg.tracing import (
    RequestContextIdGenerator,
    init_tracing,
    shutdown_tracing,
    span_context,
)

_TRACE_ID = "019da8cd058b76ed8a4a52141c1c6b38"


@pytest.fixture
def tracing_runtime(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(id_generator=RequestContextIdGenerator())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        otel_runtime.request_context,
        "get_trace_id",
        lambda: _TRACE_ID,
    )
    init_tracing(
        enabled=True,
        service_name="test-agent-backend",
        tracer_provider=provider,
    )
    yield exporter
    shutdown_tracing()
    provider.shutdown()


@pytest.fixture(autouse=True)
def cleanup_runtime() -> None:
    shutdown_tracing()
    yield
    shutdown_tracing()
    loguru_logger.remove()


def _span_context_ids(span) -> tuple[str, str]:
    current = span.get_span_context()
    return f"{current.trace_id:032x}", f"{current.span_id:016x}"


def _configure_json_logger(tmp_path: Path) -> Path:
    base_log_dir = tmp_path / "logs"
    LoggerHandler(
        base_log_dir=base_log_dir,
        log_format=LogFormat.JSON,
        enqueue=False,
    ).setup(write_to_file=True, write_to_console=False)
    return base_log_dir


def _read_records(base_log_dir: Path) -> list[dict]:
    files = list(base_log_dir.glob("*.log"))
    assert len(files) == 1
    return [
        json.loads(line)
        for line in files[0].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.mark.asyncio
async def test_disabled_span_context_is_non_recording_and_preserves_exception() -> None:
    init_tracing(enabled=False, service_name="test-agent-backend")

    with pytest.raises(RuntimeError, match="boom"):
        async with span_context("disabled") as span:
            assert span.is_recording() is False
            raise RuntimeError("boom")

    assert trace.get_current_span().get_span_context().is_valid is False


@pytest.mark.asyncio
async def test_root_span_uses_request_context_trace_id_and_exports(
    tracing_runtime: InMemorySpanExporter,
) -> None:
    async with span_context(
        "root",
        span_kind=SpanKind.SERVER,
        attributes={"http.request.method": "GET"},
    ) as span:
        trace_id, span_id = _span_context_ids(span)
        assert trace_id == _TRACE_ID
        assert len(span_id) == 16
        assert int(span_id, 16) != 0
        assert trace.get_current_span() is span

    assert trace.get_current_span().get_span_context().is_valid is False
    finished = tracing_runtime.get_finished_spans()
    assert len(finished) == 1
    assert finished[0].name == "root"
    assert finished[0].kind is SpanKind.SERVER
    assert finished[0].parent is None
    assert finished[0].attributes["http.request.method"] == "GET"
    assert finished[0].instrumentation_scope.name == "pkg.tracing.span"


@pytest.mark.asyncio
async def test_nested_spans_use_real_parent_identity(
    tracing_runtime: InMemorySpanExporter,
) -> None:
    async with span_context("outer") as outer:
        outer_context = outer.get_span_context()
        async with span_context("inner") as inner:
            inner_context = inner.get_span_context()
            assert inner_context.trace_id == outer_context.trace_id
            assert inner_context.span_id != outer_context.span_id
        assert trace.get_current_span() is outer

    finished = {span.name: span for span in tracing_runtime.get_finished_spans()}
    assert finished["inner"].parent is not None
    assert finished["inner"].parent.span_id == finished["outer"].context.span_id


@pytest.mark.asyncio
async def test_exception_records_error_and_restores_parent(
    tracing_runtime: InMemorySpanExporter,
) -> None:
    async with span_context("outer") as outer:
        with pytest.raises(ValueError, match="invalid"):
            async with span_context("inner"):
                raise ValueError("invalid")
        assert trace.get_current_span() is outer

    finished = {span.name: span for span in tracing_runtime.get_finished_spans()}
    inner = finished["inner"]
    assert inner.status.status_code is StatusCode.ERROR
    assert [event.name for event in inner.events].count("exception") == 1
    assert finished["outer"].status.status_code is StatusCode.UNSET


@pytest.mark.asyncio
async def test_anyio_gather_and_task_group_children_keep_parent_context(
    tracing_runtime: InMemorySpanExporter,
) -> None:
    child_ids: set[int] = set()

    async def worker(name: str) -> None:
        async with span_context(name) as span:
            child_ids.add(span.get_span_context().span_id)
            await anyio.sleep(0)

    async with span_context("root") as root:
        root_span_id = root.get_span_context().span_id
        await anyio_gather(worker("gather.left"), worker("gather.right"))
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(worker, "anyio.left")
            task_group.start_soon(worker, "anyio.right")

    assert len(child_ids) == 4
    finished = tracing_runtime.get_finished_spans()
    children = [span for span in finished if span.name != "root"]
    assert len(children) == 4
    assert all(span.parent is not None for span in children)
    assert all(span.parent.span_id == root_span_id for span in children if span.parent)


@pytest.mark.asyncio
async def test_json_logs_use_current_otel_trace_id_without_span_id(
    tmp_path: Path,
    tracing_runtime: InMemorySpanExporter,
) -> None:
    base_log_dir = _configure_json_logger(tmp_path)

    async with span_context("logged") as span:
        expected_trace_id, _ = _span_context_ids(span)
        loguru_logger.info("inside")
    loguru_logger.info("outside")
    loguru_logger.complete()

    records = {record["message"]: record for record in _read_records(base_log_dir)}
    assert records["inside"]["trace_id"] == expected_trace_id
    assert "span_id" not in records["inside"]
    assert records["outside"]["trace_id"] == _TRACE_ID
    assert "span_id" not in records["outside"]
    assert "span.start" not in records
    assert "span.end" not in records


def test_span_context_rejects_sync_usage_and_invalid_arguments() -> None:
    with pytest.raises(TypeError, match="async with"):
        with span_context("sync"):
            pass

    with pytest.raises(ValueError, match="span_name cannot be empty"):
        span_context("  ")

    with pytest.raises(ValueError, match="span_name must contain only"):
        span_context("bad/name")

    dynamic_span_context: Any = span_context

    with pytest.raises(TypeError, match="span_kind must be a SpanKind"):
        dynamic_span_context("bad.kind", span_kind="SERVER")

    with pytest.raises(TypeError, match="attributes must be a mapping"):
        dynamic_span_context("bad.attributes", attributes=[])
