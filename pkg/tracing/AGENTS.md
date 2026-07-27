# AGENTS.md

适用于 `pkg/tracing/`。父级约束见 `pkg/AGENTS.md`。

## 模块职责

基于 OpenTelemetry 的进程级 tracing runtime 与异步 Span 门面。

- `otel.py`：管理 `TracerProvider`、`Tracer`、Root Trace ID Generator 和进程生命周期。
- `span.py`：只提供 `span_context` 异步上下文门面；不得恢复装饰器或自研 Span 树。
- `__init__.py`：集中暴露 tracing 公共 API；业务调用方优先从这里导入。

## 使用协议

- FastAPI lifespan 和 Celery Worker hook 负责调用 `init_tracing()` / `shutdown_tracing()`。
- 业务埋点统一使用 `async with span_context(...)`；是否创建真实 Span 由 tracing runtime 配置决定。
- 调用点不得直接判断 tracing 开关，也不得用 `logger.bind(...)` 模拟 Span。
- 模块不得依赖 `pkg.logger`；logger handler 只从 `pkg.request_context` 读取日志 `trace_id`，Root Span 通过
  `RequestContextIdGenerator` 复用同一 ID 完成关联。

## 兼容性要求

- `init_tracing`、`shutdown_tracing`、`is_tracing_enabled` 和 `span_context` 是共享基础设施 contract，改动前先全仓检索。
- tracing API 只从 `pkg.tracing` 暴露；不得在 `pkg.logger` 中增加 tracing re-export。
- Instrumentation scope name 会出现在导出的 Span 数据中；修改时需要评估查询、仪表盘和告警兼容性。

## 验证重点

- 启动期 init、多次 init 的幂等性、冲突配置和未启用场景的降级。
- Span 在异步任务切换、Celery Worker、FastAPI 请求三条链路的上下文继承。
- Root Span 对合法请求 trace ID 的复用、异常状态记录和 Provider 所有权清理。
