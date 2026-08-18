"""OpenTelemetry Span 模拟 Service。"""

from __future__ import annotations

from functools import cache

import anyio
from opentelemetry import trace

from internal.core import AppException, errors
from internal.schemas.logger_span import SpanSimulateDTO, SpanTraceDTO
from internal.services.logger_span_mock import MockTraceBuilder
from pkg.logger import logger
from pkg.tracing import span_context

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


class _SimulatedFailure(RuntimeError):
    """Service 内部使用的模拟异常。"""

    __slots__ = ("step",)

    def __init__(self, step: str) -> None:
        super().__init__(f"simulated failure at step={step}")
        self.step = step


def _format_trace_id(value: int) -> str:
    return f"{value:032x}"


def _is_valid_trace_id(value: str) -> bool:
    if len(value) != 32:
        return False
    try:
        parsed = int(value, 16)
    except ValueError:
        return False
    return parsed != trace.INVALID_TRACE_ID


def _maybe_raise(step: str, fail_at: str | None) -> None:
    if fail_at == step:
        raise _SimulatedFailure(step)


class LoggerSpanService:
    """模拟 OpenTelemetry 调用链。"""

    async def simulate_trace(
        self,
        *,
        scenario: str,
        fail_at: str | None,
    ) -> SpanSimulateDTO:
        normalized_fail_at = fail_at.strip() if fail_at else None
        if normalized_fail_at and normalized_fail_at not in _SUPPORTED_FAIL_STEPS:
            logger.info(
                f"logger_span.simulate ignore unsupported fail_at={normalized_fail_at}"
            )
            normalized_fail_at = None

        try:
            async with span_context(_ROOT_SPAN_NAME) as root_span:
                root_context = root_span.get_span_context()
                if not root_context.is_valid:
                    raise AppException(
                        errors.ServiceUnavailable,
                        message="OpenTelemetry tracing is disabled",
                    )
                trace_id = _format_trace_id(root_context.trace_id)
                await self._run_pipeline(fail_at=normalized_fail_at)
        except _SimulatedFailure as exc:
            logger.info(
                f"logger_span.simulate stopped at step={exc.step} scenario={scenario}"
            )

        return SpanSimulateDTO(trace_id=trace_id)

    async def get_trace(self, *, trace_id: str) -> SpanTraceDTO:
        normalized_trace_id = trace_id.strip().lower() if trace_id else ""
        if not _is_valid_trace_id(normalized_trace_id):
            raise AppException(
                errors.BadRequest,
                message="trace_id must be a non-zero 32-character hexadecimal string",
            )

        return MockTraceBuilder.build(trace_id=normalized_trace_id)

    async def _run_pipeline(
        self,
        *,
        fail_at: str | None,
    ) -> None:
        async with span_context("otel.demo.validate"):
            await anyio.sleep(0.005)
            _maybe_raise(_STEP_VALIDATE, fail_at)

        async with span_context("otel.demo.db.read"):
            async with span_context("otel.demo.db.query.users"):
                await anyio.sleep(0.01)
            async with span_context("otel.demo.db.query.orders"):
                await anyio.sleep(0.015)
            _maybe_raise(_STEP_DB_READ, fail_at)

        async with span_context("otel.demo.llm.intent"):
            async with span_context("otel.demo.llm.intent.prompt.build"):
                await anyio.sleep(0.004)
            async with span_context("otel.demo.llm.intent.request"):
                await anyio.sleep(0.016)
            _maybe_raise(_STEP_LLM_INTENT, fail_at)

        async with span_context("otel.demo.rag.recall"):
            async with span_context("otel.demo.rag.recall.embed"):
                await anyio.sleep(0.008)
            async with span_context("otel.demo.rag.recall.vector.search"):
                await anyio.sleep(0.02)
            async with span_context("otel.demo.rag.recall.rerank"):
                await anyio.sleep(0.012)
            _maybe_raise(_STEP_RAG_RECALL, fail_at)

        async with span_context("otel.demo.llm.business"):
            async with span_context("otel.demo.llm.business.prompt.build"):
                await anyio.sleep(0.006)
            async with span_context("otel.demo.llm.business.request"):
                await anyio.sleep(0.026)
            _maybe_raise(_STEP_LLM_BUSINESS, fail_at)

        async with span_context("otel.demo.cache.write"):
            await anyio.sleep(0.003)
            _maybe_raise(_STEP_CACHE_WRITE, fail_at)


@cache
def new_logger_span_service() -> LoggerSpanService:
    """依赖注入：获取 LoggerSpanService 单例。"""
    return LoggerSpanService()
