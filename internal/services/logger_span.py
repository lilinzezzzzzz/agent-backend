"""OpenTelemetry Span 模拟 Service。"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Lock

import anyio
from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind

from internal.core import AppException, errors
from internal.schemas.logger_span import SpanNodeDTO, SpanSimulateDTO, SpanStatus
from pkg.logger import logger, span_context
from pkg.logger.otel import is_tracing_enabled

_ROOT_SPAN_NAME = "otel.demo.simulate.root"
_STEP_VALIDATE = "validate"
_STEP_DB_READ = "db.read"
_STEP_LLM_INTENT = "llm.intent"
_STEP_RAG_RECALL = "rag.recall"
_STEP_LLM_BUSINESS = "llm.business"
_STEP_CACHE_WRITE = "cache.write"

_SUPPORTED_FAIL_STEPS: frozenset[str] = frozenset(
    {
        _STEP_VALIDATE,
        _STEP_DB_READ,
        _STEP_LLM_INTENT,
        _STEP_RAG_RECALL,
        _STEP_LLM_BUSINESS,
        _STEP_CACHE_WRITE,
    }
)


@dataclass(frozen=True, slots=True)
class _PipelineStep:
    span_name: str
    parent_name: str | None
    work_ms: int
    fail_step: str | None


_PIPELINE_STEPS: tuple[_PipelineStep, ...] = (
    _PipelineStep(_ROOT_SPAN_NAME, None, 0, None),
    _PipelineStep("otel.demo.validate", _ROOT_SPAN_NAME, 12, _STEP_VALIDATE),
    _PipelineStep("otel.demo.db.read", _ROOT_SPAN_NAME, 30, _STEP_DB_READ),
    _PipelineStep("otel.demo.db.query.users", "otel.demo.db.read", 11, None),
    _PipelineStep("otel.demo.db.query.orders", "otel.demo.db.read", 16, None),
    _PipelineStep("otel.demo.llm.intent", _ROOT_SPAN_NAME, 22, _STEP_LLM_INTENT),
    _PipelineStep("otel.demo.llm.intent.prompt.build", "otel.demo.llm.intent", 4, None),
    _PipelineStep("otel.demo.llm.intent.request", "otel.demo.llm.intent", 16, None),
    _PipelineStep("otel.demo.rag.recall", _ROOT_SPAN_NAME, 45, _STEP_RAG_RECALL),
    _PipelineStep("otel.demo.rag.recall.embed", "otel.demo.rag.recall", 8, None),
    _PipelineStep(
        "otel.demo.rag.recall.vector.search", "otel.demo.rag.recall", 20, None
    ),
    _PipelineStep("otel.demo.rag.recall.rerank", "otel.demo.rag.recall", 12, None),
    _PipelineStep("otel.demo.llm.business", _ROOT_SPAN_NAME, 35, _STEP_LLM_BUSINESS),
    _PipelineStep(
        "otel.demo.llm.business.prompt.build", "otel.demo.llm.business", 6, None
    ),
    _PipelineStep("otel.demo.llm.business.request", "otel.demo.llm.business", 26, None),
    _PipelineStep("otel.demo.cache.write", _ROOT_SPAN_NAME, 4, _STEP_CACHE_WRITE),
)


class _SimulatedFailure(RuntimeError):
    """Service 内部使用的模拟异常。"""

    __slots__ = ("step",)

    def __init__(self, step: str) -> None:
        super().__init__(f"simulated failure at step={step}")
        self.step = step


@dataclass(slots=True)
class _SpanCollector:
    """采集当前模拟请求创建的 OTel Span 元数据。"""

    nodes: list[SpanNodeDTO] = field(default_factory=list)

    def record(self, node: SpanNodeDTO) -> None:
        self.nodes.append(node)

    def sorted_by_started_at(self) -> tuple[SpanNodeDTO, ...]:
        return tuple(sorted(self.nodes, key=lambda node: node.started_at))


def _format_trace_id(value: int) -> str:
    return f"{value:032x}"


def _format_span_id(value: int) -> str:
    return f"{value:016x}"


def _is_valid_trace_id(value: str) -> bool:
    if len(value) != 32:
        return False
    try:
        parsed = int(value, 16)
    except ValueError:
        return False
    return parsed != trace.INVALID_TRACE_ID


@asynccontextmanager
async def _tracked_span(
    collector: _SpanCollector,
    span_name: str,
) -> AsyncIterator[Span]:
    """为 demo 收集展示字段，真实 Span 生命周期仍由 `span_context` 管理。"""

    parent_context = trace.get_current_span().get_span_context()
    parent_span_id = (
        _format_span_id(parent_context.span_id) if parent_context.is_valid else None
    )
    started_at = datetime.now(UTC)
    started_perf = time.perf_counter()
    error_type: str | None = None

    async with span_context(span_name) as span:
        span_context_value = span.get_span_context()
        if not span_context_value.is_valid:
            raise RuntimeError(
                "Tracing enabled but span_context returned an invalid Span"
            )
        try:
            yield span
        except BaseException as exc:
            error_type = type(exc).__name__
            raise
        finally:
            elapsed_ms = round((time.perf_counter() - started_perf) * 1000, 3)
            collector.record(
                SpanNodeDTO(
                    trace_id=_format_trace_id(span_context_value.trace_id),
                    span_id=_format_span_id(span_context_value.span_id),
                    parent_span_id=parent_span_id,
                    span_name=span_name,
                    span_kind=SpanKind.INTERNAL.name,
                    status=SpanStatus.ERROR if error_type else SpanStatus.OK,
                    elapsed_ms=elapsed_ms,
                    started_at=started_at,
                    finished_at=started_at + timedelta(milliseconds=elapsed_ms),
                    error_type=error_type,
                )
            )


def _maybe_raise(step: str, fail_at: str | None) -> None:
    if fail_at == step:
        raise _SimulatedFailure(step)


_TRACE_STORE_MAX_ENTRIES = 200


class _TraceStore:
    """保存最近 Trace 的进程内有界 LRU。"""

    def __init__(self, *, max_entries: int = _TRACE_STORE_MAX_ENTRIES) -> None:
        self._max_entries = max_entries
        self._items: OrderedDict[str, SpanSimulateDTO] = OrderedDict()
        self._lock = Lock()

    def put(self, dto: SpanSimulateDTO) -> None:
        with self._lock:
            self._items[dto.trace_id] = dto
            self._items.move_to_end(dto.trace_id)
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)

    def get(self, trace_id: str) -> SpanSimulateDTO | None:
        with self._lock:
            dto = self._items.get(trace_id)
            if dto is not None:
                self._items.move_to_end(trace_id)
            return dto


def _deterministic_span_id(trace_id: str, span_name: str) -> str:
    value = int.from_bytes(
        hashlib.sha256(f"{trace_id}:{span_name}".encode()).digest()[:8], "big"
    )
    return f"{value or 1:016x}"


def _synthesize_mock_trace(trace_id: str) -> SpanSimulateDTO:
    """基于合法 Trace ID 合成确定性的 OTel 父子结构。"""

    digest = hashlib.sha256(trace_id.encode()).digest()
    fail_options = (
        _STEP_VALIDATE,
        _STEP_DB_READ,
        _STEP_LLM_INTENT,
        _STEP_RAG_RECALL,
        _STEP_LLM_BUSINESS,
        _STEP_CACHE_WRITE,
        None,
    )
    fail_at = fail_options[digest[0] % len(fail_options)]
    scenario = f"mock-{digest[1]:02x}"
    base_started_at = datetime.now(UTC) - timedelta(seconds=60 + digest[2] % 240)

    failed_index = next(
        (
            index
            for index, step in enumerate(_PIPELINE_STEPS)
            if step.fail_step == fail_at
        ),
        None,
    )
    if failed_index is None:
        active_steps = list(_PIPELINE_STEPS)
    else:
        failed_name = _PIPELINE_STEPS[failed_index].span_name
        included_names = {
            step.span_name for step in _PIPELINE_STEPS[: failed_index + 1]
        }
        for step in _PIPELINE_STEPS:
            parent_name = step.parent_name
            while parent_name is not None:
                if parent_name == failed_name:
                    included_names.add(step.span_name)
                    break
                parent_name = next(
                    candidate.parent_name
                    for candidate in _PIPELINE_STEPS
                    if candidate.span_name == parent_name
                )
        active_steps = [
            step for step in _PIPELINE_STEPS if step.span_name in included_names
        ]

    children_by_parent: dict[str, list[_PipelineStep]] = {}
    for step in active_steps:
        if step.parent_name is not None:
            children_by_parent.setdefault(step.parent_name, []).append(step)

    start_offsets: dict[str, int] = {}
    end_offsets: dict[str, int] = {}
    running_ms = 0

    def assign_time(step: _PipelineStep) -> None:
        nonlocal running_ms
        start_offsets[step.span_name] = running_ms
        for child in children_by_parent.get(step.span_name, []):
            assign_time(child)
        running_ms += step.work_ms
        end_offsets[step.span_name] = running_ms

    assign_time(active_steps[0])

    error_names: set[str] = set()
    if fail_at is not None:
        failed_step = next(
            (step for step in _PIPELINE_STEPS if step.fail_step == fail_at), None
        )
        while failed_step is not None:
            error_names.add(failed_step.span_name)
            if failed_step.parent_name is None:
                break
            failed_step = next(
                step
                for step in _PIPELINE_STEPS
                if step.span_name == failed_step.parent_name
            )

    span_ids = {
        step.span_name: _deterministic_span_id(trace_id, step.span_name)
        for step in active_steps
    }
    spans: list[SpanNodeDTO] = []
    for step in active_steps:
        started_at = base_started_at + timedelta(
            milliseconds=start_offsets[step.span_name]
        )
        elapsed_ms = float(end_offsets[step.span_name] - start_offsets[step.span_name])
        is_error = step.span_name in error_names
        spans.append(
            SpanNodeDTO(
                trace_id=trace_id,
                span_id=span_ids[step.span_name],
                parent_span_id=(
                    span_ids.get(step.parent_name)
                    if step.parent_name is not None
                    else None
                ),
                span_name=step.span_name,
                span_kind=SpanKind.INTERNAL.name,
                status=SpanStatus.ERROR if is_error else SpanStatus.OK,
                elapsed_ms=elapsed_ms,
                started_at=started_at,
                finished_at=started_at + timedelta(milliseconds=elapsed_ms),
                error_type="_SimulatedFailure" if is_error else None,
            )
        )

    root_span = spans[0]
    return SpanSimulateDTO(
        scenario=scenario,
        trace_id=trace_id,
        root_span_id=root_span.span_id,
        root_span_name=root_span.span_name,
        elapsed_ms=float(end_offsets[_ROOT_SPAN_NAME]),
        failed_step=fail_at,
        spans=tuple(spans),
    )


class LoggerSpanService:
    """模拟 OpenTelemetry 调用链。"""

    def __init__(self, *, trace_store: _TraceStore | None = None) -> None:
        self._trace_store = trace_store or _TraceStore()

    async def simulate_trace(
        self,
        *,
        scenario: str,
        fail_at: str | None,
    ) -> SpanSimulateDTO:
        if not is_tracing_enabled():
            raise AppException(
                errors.ServiceUnavailable,
                message="OpenTelemetry tracing is disabled",
            )

        normalized_fail_at = fail_at.strip() if fail_at else None
        if normalized_fail_at and normalized_fail_at not in _SUPPORTED_FAIL_STEPS:
            logger.info(
                f"logger_span.simulate ignore unsupported fail_at={normalized_fail_at}"
            )
            normalized_fail_at = None

        collector = _SpanCollector()
        started_perf = time.perf_counter()
        failed_step: str | None = None
        root_span_id = ""
        trace_id = ""

        try:
            async with _tracked_span(collector, _ROOT_SPAN_NAME) as root_span:
                root_context = root_span.get_span_context()
                trace_id = _format_trace_id(root_context.trace_id)
                root_span_id = _format_span_id(root_context.span_id)
                await self._run_pipeline(
                    collector=collector, fail_at=normalized_fail_at
                )
        except _SimulatedFailure as exc:
            failed_step = exc.step
            logger.info(
                f"logger_span.simulate stopped at step={exc.step} scenario={scenario}"
            )

        dto = SpanSimulateDTO(
            scenario=scenario,
            trace_id=trace_id,
            root_span_id=root_span_id,
            root_span_name=_ROOT_SPAN_NAME,
            elapsed_ms=round((time.perf_counter() - started_perf) * 1000, 3),
            failed_step=failed_step,
            spans=collector.sorted_by_started_at(),
        )
        self._trace_store.put(dto)
        return dto

    async def get_trace(self, *, trace_id: str) -> SpanSimulateDTO:
        normalized_trace_id = trace_id.strip().lower() if trace_id else ""
        if not _is_valid_trace_id(normalized_trace_id):
            raise AppException(
                errors.BadRequest,
                message="trace_id must be a non-zero 32-character hexadecimal string",
            )

        stored = self._trace_store.get(normalized_trace_id)
        if stored is not None:
            return stored
        return _synthesize_mock_trace(normalized_trace_id)

    async def _run_pipeline(
        self,
        *,
        collector: _SpanCollector,
        fail_at: str | None,
    ) -> None:
        async with _tracked_span(collector, "otel.demo.validate"):
            await anyio.sleep(0.005)
            _maybe_raise(_STEP_VALIDATE, fail_at)

        async with _tracked_span(collector, "otel.demo.db.read"):
            async with _tracked_span(collector, "otel.demo.db.query.users"):
                await anyio.sleep(0.01)
            async with _tracked_span(collector, "otel.demo.db.query.orders"):
                await anyio.sleep(0.015)
            _maybe_raise(_STEP_DB_READ, fail_at)

        async with _tracked_span(collector, "otel.demo.llm.intent"):
            async with _tracked_span(collector, "otel.demo.llm.intent.prompt.build"):
                await anyio.sleep(0.004)
            async with _tracked_span(collector, "otel.demo.llm.intent.request"):
                await anyio.sleep(0.016)
            _maybe_raise(_STEP_LLM_INTENT, fail_at)

        async with _tracked_span(collector, "otel.demo.rag.recall"):
            async with _tracked_span(collector, "otel.demo.rag.recall.embed"):
                await anyio.sleep(0.008)
            async with _tracked_span(collector, "otel.demo.rag.recall.vector.search"):
                await anyio.sleep(0.02)
            async with _tracked_span(collector, "otel.demo.rag.recall.rerank"):
                await anyio.sleep(0.012)
            _maybe_raise(_STEP_RAG_RECALL, fail_at)

        async with _tracked_span(collector, "otel.demo.llm.business"):
            async with _tracked_span(collector, "otel.demo.llm.business.prompt.build"):
                await anyio.sleep(0.006)
            async with _tracked_span(collector, "otel.demo.llm.business.request"):
                await anyio.sleep(0.026)
            _maybe_raise(_STEP_LLM_BUSINESS, fail_at)

        async with _tracked_span(collector, "otel.demo.cache.write"):
            await anyio.sleep(0.003)
            _maybe_raise(_STEP_CACHE_WRITE, fail_at)


_logger_span_service: LoggerSpanService | None = None


def new_logger_span_service() -> LoggerSpanService:
    """依赖注入：获取 LoggerSpanService 单例。"""

    global _logger_span_service
    if _logger_span_service is None:
        _logger_span_service = LoggerSpanService()
    return _logger_span_service
