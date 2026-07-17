import pytest
from opentelemetry.context import Context, attach, detach
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import INVALID_SPAN_CONTEXT, NonRecordingSpan, use_span

from internal.core import AppException, errors
from internal.services.logger_span import LoggerSpanService
import pkg.logger.otel as otel_runtime
from pkg.logger.otel import RequestContextIdGenerator, init_tracing, shutdown_tracing

_TRACE_ID = "019da8cd058b76ed8a4a52141c1c6b38"


@pytest.fixture(autouse=True)
def cleanup_runtime() -> None:
    context_token = attach(Context())
    shutdown_tracing()
    yield
    shutdown_tracing()
    detach(context_token)


@pytest.fixture
def tracing_runtime(monkeypatch: pytest.MonkeyPatch) -> TracerProvider:
    provider = TracerProvider(id_generator=RequestContextIdGenerator())
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
    yield provider
    shutdown_tracing()
    provider.shutdown()


@pytest.mark.asyncio
async def test_simulate_trace_returns_real_otel_identity(
    tracing_runtime: TracerProvider,
) -> None:
    # pytest-asyncio 会继承创建 Task 时的 Context；显式覆盖可避免前序测试残留 parent。
    with use_span(NonRecordingSpan(INVALID_SPAN_CONTEXT)):
        result = await LoggerSpanService().simulate_trace(
            scenario="success",
            fail_at=None,
        )

    assert result.trace_id == _TRACE_ID
    assert len(result.root_span_id) == 16
    assert len(result.spans) == 16
    by_id = {span.span_id: span for span in result.spans}
    root = by_id[result.root_span_id]
    assert root.span_name == "otel.demo.simulate.root"
    assert all(span.trace_id == _TRACE_ID for span in result.spans)
    assert all(
        span.parent_span_id is None or span.parent_span_id in by_id
        for span in result.spans
        if span.span_id != result.root_span_id
    )


@pytest.mark.asyncio
async def test_simulate_trace_rejects_disabled_tracing() -> None:
    init_tracing(enabled=False, service_name="test-agent-backend")

    with pytest.raises(AppException) as exc_info:
        await LoggerSpanService().simulate_trace(scenario="disabled", fail_at=None)

    assert exc_info.value.error == errors.ServiceUnavailable


@pytest.mark.asyncio
async def test_get_trace_builds_deterministic_span_id_tree() -> None:
    service = LoggerSpanService()

    first = await service.get_trace(trace_id=_TRACE_ID)
    second = await service.get_trace(trace_id=_TRACE_ID.upper())

    assert first.trace_id == _TRACE_ID
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
