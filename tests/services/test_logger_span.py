import pytest
from opentelemetry.context import Context, attach, detach
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import (
    INVALID_SPAN_CONTEXT,
    NonRecordingSpan,
    StatusCode,
    use_span,
)

from internal.core import AppException, errors
from internal.services.logger_span import LoggerSpanService
import pkg.tracing.otel as otel_runtime
from pkg.tracing import RequestContextIdGenerator, init_tracing, shutdown_tracing

_TRACE_ID = "019da8cd058b76ed8a4a52141c1c6b38"


@pytest.fixture(autouse=True)
def cleanup_runtime() -> None:
    context_token = attach(Context())
    shutdown_tracing()
    yield
    shutdown_tracing()
    detach(context_token)


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


@pytest.mark.asyncio
async def test_simulate_trace_returns_real_otel_identity(
    tracing_runtime: InMemorySpanExporter,
) -> None:
    # pytest-asyncio 会继承创建 Task 时的 Context；显式覆盖可避免前序测试残留 parent。
    with use_span(NonRecordingSpan(INVALID_SPAN_CONTEXT)):
        result = await LoggerSpanService().simulate_trace(
            scenario="success",
            fail_at=None,
        )

    assert result.trace_id == _TRACE_ID
    assert result.to_schema().model_dump() == {"trace_id": _TRACE_ID}

    finished = tracing_runtime.get_finished_spans()
    assert len(finished) == 16
    assert all(span.context is not None for span in finished)
    assert all(
        f"{span.context.trace_id:032x}" == _TRACE_ID
        for span in finished
        if span.context
    )
    assert {span.name for span in finished} >= {
        "otel.demo.simulate.root",
        "otel.demo.validate",
        "otel.demo.rag.recall.vector.search",
        "otel.demo.llm.business.request",
    }


@pytest.mark.asyncio
async def test_simulate_trace_rejects_disabled_tracing() -> None:
    init_tracing(enabled=False, service_name="test-agent-backend")

    with pytest.raises(AppException) as exc_info:
        await LoggerSpanService().simulate_trace(scenario="disabled", fail_at=None)

    assert exc_info.value.error == errors.ServiceUnavailable


@pytest.mark.asyncio
async def test_simulate_trace_returns_trace_id_after_simulated_failure(
    tracing_runtime: InMemorySpanExporter,
) -> None:
    result = await LoggerSpanService().simulate_trace(
        scenario="failure",
        fail_at="validate",
    )

    assert result.trace_id == _TRACE_ID
    finished = {span.name: span for span in tracing_runtime.get_finished_spans()}
    assert set(finished) == {"otel.demo.validate", "otel.demo.simulate.root"}
    assert finished["otel.demo.validate"].status.status_code is StatusCode.ERROR
    assert finished["otel.demo.simulate.root"].status.status_code is StatusCode.ERROR


@pytest.mark.asyncio
async def test_get_trace_builds_deterministic_mock_span_tree() -> None:
    service = LoggerSpanService()

    first = await service.get_trace(trace_id=_TRACE_ID)
    second = await service.get_trace(trace_id=_TRACE_ID.upper())

    assert first.trace_id == _TRACE_ID
    assert first.scenario.startswith("mock-")
    assert first.to_schema().span_count == len(first.spans)
    assert first.root_span_id == second.root_span_id
    assert [span.span_id for span in first.spans] == [
        span.span_id for span in second.spans
    ]
    span_ids = {span.span_id for span in first.spans}
    assert all(
        span.parent_span_id is None or span.parent_span_id in span_ids
        for span in first.spans
    )


@pytest.mark.asyncio
async def test_get_trace_rejects_invalid_trace_id() -> None:
    with pytest.raises(AppException) as exc_info:
        await LoggerSpanService().get_trace(trace_id="demo-trace")

    assert exc_info.value.error == errors.BadRequest
