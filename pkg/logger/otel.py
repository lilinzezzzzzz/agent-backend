"""OpenTelemetry tracing runtime。"""

from dataclasses import dataclass
from threading import Lock

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.id_generator import RandomIdGenerator
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased
from opentelemetry.trace import Tracer

from pkg import request_context

_INSTRUMENTATION_SCOPE_NAME = "pkg.logger.span"


class RequestContextIdGenerator(RandomIdGenerator):
    """Root Span 优先复用请求上下文中的合法 W3C Trace ID。"""

    def generate_trace_id(self) -> int:
        try:
            candidate = request_context.get_trace_id()
        except LookupError:
            return super().generate_trace_id()

        if len(candidate) != 32:
            return super().generate_trace_id()

        try:
            trace_id = int(candidate, 16)
        except ValueError:
            return super().generate_trace_id()

        if trace_id == trace.INVALID_TRACE_ID:
            return super().generate_trace_id()
        return trace_id


@dataclass(frozen=True, slots=True)
class TracingRuntime:
    """进程级 tracing 配置与资源所有权。"""

    enabled: bool
    service_name: str
    provider: TracerProvider | None
    tracer: Tracer | None
    owns_provider: bool


_runtime: TracingRuntime | None = None
_runtime_lock = Lock()


def init_tracing(
    *,
    enabled: bool,
    service_name: str,
    tracer_provider: TracerProvider | None = None,
) -> None:
    """初始化进程级 tracing runtime。

    ``tracer_provider`` 是测试和宿主进程集成边界；调用方传入时，本模块不安装全局
    Provider，也不在 shutdown 时关闭它。
    """

    normalized_service_name = service_name.strip()
    if not normalized_service_name:
        raise ValueError("service_name cannot be empty")
    if not enabled and tracer_provider is not None:
        raise ValueError("tracer_provider requires enabled tracing")

    global _runtime
    with _runtime_lock:
        if _runtime is not None:
            same_provider = (
                tracer_provider is None or tracer_provider is _runtime.provider
            )
            if (
                _runtime.enabled == enabled
                and _runtime.service_name == normalized_service_name
                and same_provider
            ):
                return
            raise RuntimeError(
                "Tracing runtime already initialized with different configuration"
            )

        if not enabled:
            _runtime = TracingRuntime(
                enabled=False,
                service_name=normalized_service_name,
                provider=None,
                tracer=None,
                owns_provider=False,
            )
            return

        owns_provider = tracer_provider is None
        provider = tracer_provider or TracerProvider(
            sampler=ParentBased(ALWAYS_ON),
            resource=Resource.create({"service.name": normalized_service_name}),
            shutdown_on_exit=False,
            id_generator=RequestContextIdGenerator(),
        )

        if owns_provider:
            trace.set_tracer_provider(provider)
            if trace.get_tracer_provider() is not provider:
                provider.shutdown()
                raise RuntimeError(
                    "A global OpenTelemetry TracerProvider is already installed"
                )

        _runtime = TracingRuntime(
            enabled=True,
            service_name=normalized_service_name,
            provider=provider,
            tracer=provider.get_tracer(_INSTRUMENTATION_SCOPE_NAME),
            owns_provider=owns_provider,
        )


def shutdown_tracing() -> None:
    """关闭本模块拥有的 Provider，并清理 runtime 引用。"""

    global _runtime
    with _runtime_lock:
        runtime = _runtime
        _runtime = None

    if runtime is not None and runtime.provider is not None and runtime.owns_provider:
        runtime.provider.shutdown()


def is_tracing_enabled() -> bool:
    """返回当前进程是否已启用 tracing。"""

    runtime = _runtime
    return runtime is not None and runtime.enabled


def get_tracer() -> Tracer:
    """返回已初始化的 Tracer。"""

    runtime = _runtime
    if runtime is None or not runtime.enabled or runtime.tracer is None:
        raise RuntimeError(
            "Tracing is not enabled. Call init_tracing(enabled=True, ...) first."
        )
    return runtime.tracer


__all__ = [
    "RequestContextIdGenerator",
    "get_tracer",
    "init_tracing",
    "is_tracing_enabled",
    "shutdown_tracing",
]
