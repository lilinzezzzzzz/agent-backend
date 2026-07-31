# AGENTS.md

`pkg/tracing/` 管理 OpenTelemetry runtime 和异步 `span_context` 门面。

- FastAPI lifespan 和 Celery Worker hook 拥有 `init_tracing()` / `shutdown_tracing()`；调用点只使用
  `async with span_context(...)`，不自行判断开关或模拟 Span。
- tracing 不依赖 `pkg.logger`；Root Span 通过 request context 复用合法请求 trace ID。
- 公共 tracing API、instrumentation scope name 和 trace 关联语义是可观测性 contract。
- 初始化和关闭保持幂等，取消和异常状态正确传播，且只清理本模块拥有的 provider。
- 变化覆盖 FastAPI、Celery、异步 task 切换及未启用场景。
