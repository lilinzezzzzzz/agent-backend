# AGENTS.md

适用于 `pkg/logger/`。父级约束见 `pkg/AGENTS.md`。

## 模块职责

基于 Loguru 的统一日志封装。

- `handler.py`：`LoggerHandler`，封装格式、分文件、rotation、retention、tz。
- `otel.py`：OpenTelemetry TracerProvider、Tracer、Root Trace ID Generator 和进程生命周期。
- `span.py`：只提供 `span_context` 异步上下文门面；不得恢复装饰器或自研 Span 树。
- `__init__.py`：通过 `pkg.lazy_proxy.lazy_proxy` 暴露延迟初始化的 `logger` 和 `init_logger`。

## 使用协议

- `init_logger()` 必须在应用启动 lifespan 里调用；`logger` 是 `lazy_proxy`，在 init 之前访问会触发延迟解析（测试中需要 `logger_mock` fixture 预置）。
- 模块顶层不写日志；所有日志必须在函数/类方法内部产生，避免 import 副作用绑定未初始化 logger。
- 业务埋点统一使用 `async with span_context(...)`；是否创建真实 Span 由 tracing runtime 配置决定，
  调用点不得直接判断开关或拼 `logger.bind(...)` 模拟 Span。

## 编码约定

- 新增字段先扩展 `LogFormat` / `LoggerHandler` 的参数；不要在业务代码里散落自定义 format string。
- rotation / retention 策略通过枚举或类型别名表达，避免在调用点硬编码 `"10 MB"` / `"7 days"` 字符串。
- 不允许在日志 message 或 extra 里出现 token、密码、密钥、连接串明文；必要时先脱敏再落日志。
- 若要在 `pkg/` 其他基础包中使用，优先接受 logger 注入（参考 `pkg/decorators/`），保持基础包可独立使用。

## 兼容性要求

- `logger` 名称、`init_logger` 签名和 `span_context` 接口被 `internal/app.py`、中间件、Celery 任务广泛依赖，
  改动前先全仓检索并补测试。
- 日志文件路径、命名、rotation 粒度属于运维契约，变更需要同步 `configs/` 与运维文档。

## 验证重点

- 启动期 init、多次 init 的幂等性、未 init 场景的降级。
- span 在异步任务切换、Celery worker、FastAPI 请求三条链路的上下文继承。
