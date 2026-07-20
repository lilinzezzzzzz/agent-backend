"""Logger span GET 接口使用的确定性 mock Trace 构造器。"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from opentelemetry.trace import SpanKind

from internal.schemas.logger_span import SpanNodeDTO, SpanStatus, SpanTraceDTO

_ROOT_SPAN_NAME = "otel.demo.simulate.root"
_STEP_VALIDATE = "validate"
_STEP_DB_READ = "db.read"
_STEP_LLM_INTENT = "llm.intent"
_STEP_RAG_RECALL = "rag.recall"
_STEP_LLM_BUSINESS = "llm.business"
_STEP_CACHE_WRITE = "cache.write"


@dataclass(frozen=True, slots=True)
class _MockPipelineStep:
    span_name: str
    parent_name: str | None
    work_ms: int
    fail_step: str | None


_MOCK_PIPELINE_STEPS: tuple[_MockPipelineStep, ...] = (
    _MockPipelineStep(_ROOT_SPAN_NAME, None, 0, None),
    _MockPipelineStep("otel.demo.validate", _ROOT_SPAN_NAME, 12, _STEP_VALIDATE),
    _MockPipelineStep("otel.demo.db.read", _ROOT_SPAN_NAME, 30, _STEP_DB_READ),
    _MockPipelineStep("otel.demo.db.query.users", "otel.demo.db.read", 11, None),
    _MockPipelineStep("otel.demo.db.query.orders", "otel.demo.db.read", 16, None),
    _MockPipelineStep("otel.demo.llm.intent", _ROOT_SPAN_NAME, 22, _STEP_LLM_INTENT),
    _MockPipelineStep(
        "otel.demo.llm.intent.prompt.build", "otel.demo.llm.intent", 4, None
    ),
    _MockPipelineStep("otel.demo.llm.intent.request", "otel.demo.llm.intent", 16, None),
    _MockPipelineStep("otel.demo.rag.recall", _ROOT_SPAN_NAME, 45, _STEP_RAG_RECALL),
    _MockPipelineStep("otel.demo.rag.recall.embed", "otel.demo.rag.recall", 8, None),
    _MockPipelineStep(
        "otel.demo.rag.recall.vector.search", "otel.demo.rag.recall", 20, None
    ),
    _MockPipelineStep("otel.demo.rag.recall.rerank", "otel.demo.rag.recall", 12, None),
    _MockPipelineStep(
        "otel.demo.llm.business", _ROOT_SPAN_NAME, 35, _STEP_LLM_BUSINESS
    ),
    _MockPipelineStep(
        "otel.demo.llm.business.prompt.build", "otel.demo.llm.business", 6, None
    ),
    _MockPipelineStep(
        "otel.demo.llm.business.request", "otel.demo.llm.business", 26, None
    ),
    _MockPipelineStep("otel.demo.cache.write", _ROOT_SPAN_NAME, 4, _STEP_CACHE_WRITE),
)


class MockTraceBuilder:
    """根据 trace_id 构造可重复的 mock Span 树。"""

    @classmethod
    def build(cls, *, trace_id: str) -> SpanTraceDTO:
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
        active_steps = cls._active_steps(fail_at=fail_at)
        start_offsets, end_offsets = cls._time_offsets(active_steps=active_steps)
        error_names = cls._error_names(fail_at=fail_at)
        span_ids = {
            step.span_name: cls._span_id(trace_id=trace_id, span_name=step.span_name)
            for step in active_steps
        }
        spans = tuple(
            cls._build_span(
                trace_id=trace_id,
                step=step,
                span_ids=span_ids,
                start_offsets=start_offsets,
                end_offsets=end_offsets,
                base_started_at=base_started_at,
                error_names=error_names,
            )
            for step in active_steps
        )
        root_span = spans[0]
        return SpanTraceDTO(
            scenario=scenario,
            trace_id=trace_id,
            root_span_id=root_span.span_id,
            root_span_name=root_span.span_name,
            elapsed_ms=float(end_offsets[_ROOT_SPAN_NAME]),
            failed_step=fail_at,
            spans=spans,
        )

    @staticmethod
    def _span_id(*, trace_id: str, span_name: str) -> str:
        value = int.from_bytes(
            hashlib.sha256(f"{trace_id}:{span_name}".encode()).digest()[:8], "big"
        )
        return f"{value or 1:016x}"

    @staticmethod
    def _active_steps(*, fail_at: str | None) -> tuple[_MockPipelineStep, ...]:
        failed_index = next(
            (
                index
                for index, step in enumerate(_MOCK_PIPELINE_STEPS)
                if step.fail_step == fail_at
            ),
            None,
        )
        if failed_index is None:
            return _MOCK_PIPELINE_STEPS

        failed_name = _MOCK_PIPELINE_STEPS[failed_index].span_name
        included_names = {
            step.span_name for step in _MOCK_PIPELINE_STEPS[: failed_index + 1]
        }
        for step in _MOCK_PIPELINE_STEPS:
            parent_name = step.parent_name
            while parent_name is not None:
                if parent_name == failed_name:
                    included_names.add(step.span_name)
                    break
                parent_name = next(
                    candidate.parent_name
                    for candidate in _MOCK_PIPELINE_STEPS
                    if candidate.span_name == parent_name
                )
        return tuple(
            step for step in _MOCK_PIPELINE_STEPS if step.span_name in included_names
        )

    @staticmethod
    def _time_offsets(
        *, active_steps: tuple[_MockPipelineStep, ...]
    ) -> tuple[dict[str, int], dict[str, int]]:
        children_by_parent: dict[str, list[_MockPipelineStep]] = {}
        for step in active_steps:
            if step.parent_name is not None:
                children_by_parent.setdefault(step.parent_name, []).append(step)

        start_offsets: dict[str, int] = {}
        end_offsets: dict[str, int] = {}
        running_ms = 0

        def assign_time(step: _MockPipelineStep) -> None:
            nonlocal running_ms
            start_offsets[step.span_name] = running_ms
            for child in children_by_parent.get(step.span_name, []):
                assign_time(child)
            running_ms += step.work_ms
            end_offsets[step.span_name] = running_ms

        assign_time(active_steps[0])
        return start_offsets, end_offsets

    @staticmethod
    def _error_names(*, fail_at: str | None) -> set[str]:
        if fail_at is None:
            return set()

        error_names: set[str] = set()
        failed_step = next(
            (step for step in _MOCK_PIPELINE_STEPS if step.fail_step == fail_at),
            None,
        )
        while failed_step is not None:
            error_names.add(failed_step.span_name)
            if failed_step.parent_name is None:
                break
            failed_step = next(
                step
                for step in _MOCK_PIPELINE_STEPS
                if step.span_name == failed_step.parent_name
            )
        return error_names

    @staticmethod
    def _build_span(
        *,
        trace_id: str,
        step: _MockPipelineStep,
        span_ids: dict[str, str],
        start_offsets: dict[str, int],
        end_offsets: dict[str, int],
        base_started_at: datetime,
        error_names: set[str],
    ) -> SpanNodeDTO:
        started_at = base_started_at + timedelta(
            milliseconds=start_offsets[step.span_name]
        )
        elapsed_ms = float(end_offsets[step.span_name] - start_offsets[step.span_name])
        is_error = step.span_name in error_names
        return SpanNodeDTO(
            trace_id=trace_id,
            span_id=span_ids[step.span_name],
            parent_span_id=(
                span_ids.get(step.parent_name) if step.parent_name is not None else None
            ),
            span_name=step.span_name,
            span_kind=SpanKind.INTERNAL.name,
            status=SpanStatus.ERROR if is_error else SpanStatus.OK,
            elapsed_ms=elapsed_ms,
            started_at=started_at,
            finished_at=started_at + timedelta(milliseconds=elapsed_ms),
            error_type="_SimulatedFailure" if is_error else None,
        )
