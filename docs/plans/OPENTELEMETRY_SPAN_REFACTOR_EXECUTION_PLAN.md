# OpenTelemetry Span 彻底重构执行计划

状态：历史实施记录，已于 2026-07-18 实施。本计划记录的是首阶段不接入 OTLP Exporter 的实施边界；
后续代码已经接入 OTLP/HTTP Exporter，当前实现与持久化边界以
[当前项目 OpenTelemetry 接入与可观测性数据持久化](../opentelemetry/project_integration_and_persistence.md)为准。

项目已使用 OpenTelemetry Span 替换 `pkg/tracing/span.py` 中的自研 Span 树，不保留
`SpanFrame`、`span_seq`、`parent_span_seq`、`span_path` 或 `span.start` / `span.end` / `span.error`
兼容协议。验证结果见第 13 节。

## 1. 目标与关键结论

- 只保留 `span_context(...)` 作为项目统一埋点门面，业务代码统一使用
  `async with span_context(...)`；删除 `with_span(...)` 装饰器，不保留兼容入口。
- Span 的创建、父子关系、异步上下文传播、状态、异常事件和生命周期全部交给 OpenTelemetry。
- 是否埋点由 `configs/` 中的配置控制；调用点不直接读取配置，也不编写开关分支。
- `span_context` 内部读取启动时初始化的 tracing runtime：启用时创建真实 OTel Span，关闭时返回
  non-recording Span，业务控制流和异常传播保持一致。
- 当前前端不接入 OpenTelemetry。FastAPI 中间件校验 `X-Trace-ID` 并写入 `request_context`；创建
  SERVER Root Span 时，IdGenerator 从 `request_context` 读取候选 Trace ID。
- 当前阶段不解析 `traceparent` / `tracestate`，但入口设计必须允许后续按
  `traceparent > X-Trace-ID > SDK 生成` 的优先级扩展。
- 当前阶段不接入 OTLP exporter，不产生到 Collector 的网络请求。

## 2. 重构后的调用模型

业务代码保持以下用法：

```python
async with span_context("service.order.create") as span:
    ...
```

HTTP Root Span 显式声明 `SpanKind.SERVER`：

```python
async with span_context(
    "middleware.request",
    span_kind=SpanKind.SERVER,
) as span:
    ...
```

运行链路：

```text
configs/.env.{APP_ENV}
        │
        ▼
internal.config.Settings
        │
        ▼
FastAPI / Celery startup
        │
        ▼
init_tracing(enabled=..., service_name=...)
        │
        ▼
pkg.tracing.otel.TracingRuntime
        │
        ▼
span_context 内部判断
        ├── enabled=false → non-recording Span
        └── enabled=true  → OpenTelemetry SDK Span
```

配置关闭时：

- 不初始化 SDK `TracerProvider`。
- 不创建或结束 SDK Span。
- 不记录 Span exception event 或 Span status。
- 不生成任何自定义 Span 开始/结束日志。
- `X-Trace-ID` 仍作为普通请求关联 ID 写入请求上下文、访问日志和响应头。

配置开启时：

- `span_context` 创建并激活真实 OTel Span。
- 嵌套和异步任务通过 OTel Context 自动继承父 Span。
- `span_context` 退出时由 SDK 记录异常、设置状态、结束 Span 并恢复上层 Context。
- 活跃 Span 内的日志从 OTel `SpanContext` 读取 `trace_id`；普通日志不输出 `span_id`。

## 3. 配置设计

### 3.1 配置所有权

运行配置统一维护在以下文件：

```text
configs/.env.local
configs/.env.dev
configs/.env.test
configs/.env.prod
```

`internal/config.py` 只声明配置字段和类型并负责解析，不硬编码实际环境值。以下配置不是密钥，不写入
`.secrets`：

```python
class Settings(BaseSettings):
    # --- OpenTelemetry ---
    OTEL_TRACING_ENABLED: bool
    OTEL_SERVICE_NAME: str
```

各环境文件显式维护：

```dotenv
# OpenTelemetry
OTEL_TRACING_ENABLED=true
OTEL_SERVICE_NAME=agent-backend
```

建议初始值：

| 配置文件 | `OTEL_TRACING_ENABLED` | 说明 |
| --- | --- | --- |
| `configs/.env.local` | `true` | 本地开发和 logger-span demo 可直接验证 |
| `configs/.env.dev` | `true` | 开发环境启用 |
| `configs/.env.test` | `false` | 测试通过 fixture 分别覆盖 enabled / disabled |
| `configs/.env.prod` | `false` | 完成 exporter、采样和 Collector 部署设计后再启用 |

### 3.2 启动时注入

应用层只负责把配置注入基础设施，不让 `pkg/tracing` 反向依赖 `internal.config`：

```python
init_tracing(
    enabled=settings.OTEL_TRACING_ENABLED,
    service_name=settings.OTEL_SERVICE_NAME,
)
```

FastAPI lifespan 和 Celery Worker hook 分别初始化和关闭 tracing runtime。初始化和关闭都必须幂等。

## 4. 依赖与 tracing runtime

### 4.1 直接依赖

虽然当前 `uv.lock` 已通过 AgentScope 间接包含 OpenTelemetry，但项目直接 import OTel 后必须在
`pyproject.toml` 声明直接依赖：

```toml
"opentelemetry-api>=1.42.1,<2",
"opentelemetry-sdk>=1.42.1,<2",
```

本阶段不增加：

- `opentelemetry-exporter-otlp-*`
- FastAPI / ASGI 自动 instrumentation
- Celery 自动 instrumentation
- OpenTelemetry Logs SDK

### 4.2 新增 `pkg/tracing/otel.py`

该模块负责：

```python
def init_tracing(*, enabled: bool, service_name: str) -> None: ...

def shutdown_tracing() -> None: ...

def is_tracing_enabled() -> bool: ...

def get_tracer() -> trace.Tracer: ...
```

运行时建议使用不可变数据结构：

```python
@dataclass(frozen=True, slots=True)
class TracingRuntime:
    enabled: bool
    provider: TracerProvider | None
    tracer: trace.Tracer
```

约束：

- `enabled=false` 时不创建 SDK Provider。
- `enabled=true` 时安装唯一全局 SDK Provider，并创建项目 Tracer。
- 重复使用相同配置初始化应幂等；使用冲突配置重复初始化应明确失败。
- 如果已有其他组件安装了不受本模块管理的全局 SDK Provider，应在启动期明确失败，不能静默形成两套
  Provider。
- 测试必须能注入独立 Provider / Tracer，避免依赖不可重置的进程级全局状态。

### 4.3 Root Trace ID Generator

新增继承 `RandomIdGenerator` 的实现，只覆盖 Trace ID 生成：

```python
class RequestContextIdGenerator(RandomIdGenerator):
    def generate_trace_id(self) -> int:
        ...
```

规则：

- 从 `pkg.request_context.get_trace_id()` 读取候选值。
- 合法的 32 位、非全零 W3C Trace ID 转换为 128-bit 整数后返回。
- 缺失或非法时回退到 `RandomIdGenerator.generate_trace_id()`。
- 不覆盖 `generate_span_id()`；所有 Span ID 始终由 SDK 生成。
- 不构造虚假的前端父 Span。
- 未来有有效远程父上下文时，SDK 直接继承父 Trace ID，不调用 Root Trace ID Generator。

## 5. 彻底重写 `pkg/tracing/span.py`

### 5.1 删除内容

删除：

- `SpanFrame`
- `_SpanRuntime`
- 自研 Span stack / runtime `ContextVar`
- `span_seq`
- `parent_span_seq`
- `span_path`
- `with_span`
- `configure_span_logger`
- `get_current_span`
- `get_span_record_extra`
- 手工计时和 `started_at`
- `span.start` / `span.end` / `span.error` Loguru 日志
- span 模块对 Loguru logger 的依赖

### 5.2 唯一公共接口

```python
def span_context(
    span_name: str,
    *,
    span_kind: SpanKind = SpanKind.INTERNAL,
    attributes: Mapping[str, AttributeValue] | None = None,
) -> AsyncContextManager[Span]:
    ...
```

Span 名称校验和仅支持 `async with` 的约束继续保留；不再提供装饰器形式的埋点入口，旧 Span 数据结构和
日志协议也不保留。

### 5.3 enabled 路径

`_AsyncSpanContext.__aenter__()` 内部：

1. 调用 `is_tracing_enabled()`。
2. 读取 `get_tracer()`。
3. 使用 `tracer.start_as_current_span(...)` 创建同步 OTel scope。
4. 在异步 context manager 中显式调用 scope 的 `__enter__()`。
5. 返回真实 `Span`。

`__aexit__()` 将原始 `exc_type`、`exc` 和 `tb` 交给 OTel scope，依赖 SDK 完成 exception event、状态、结束和
Context 恢复，并始终返回 `False`，不吞业务异常。

### 5.4 disabled 路径

返回基于 `INVALID_SPAN_CONTEXT` 的 `NonRecordingSpan`，不调用 Tracer。调用方仍可安全执行：

```python
async with span_context("service.order.create") as span:
    assert span.is_recording() is False
```

## 6. Logger 与请求中间件调整

### 6.1 `pkg/logger/handler.py`

- 删除 `configure_span_logger()` 调用。
- 删除 `span_seq`、`parent_span_seq`、`span_path`、`span_name` 格式化和输出。
- patcher 使用 `trace.get_current_span().get_span_context()` 读取当前有效的 `trace_id`。
- 有效 OTel Span 内，日志输出 32 位 `trace_id`，不输出 `span_id`。
- 没有有效 OTel Span 时，`trace_id` 回退到 `request_context.trace_id`。
- 日志不重新维护 `parent_span_id`；真实父子关系由 OTel Span 数据表达。

### 6.2 `internal/middlewares/recorder.py`

中间件继续先处理请求关联 ID：

```text
合法 UUIDv7 hex X-Trace-ID → 写入 request_context
缺失 X-Trace-ID            → 生成 UUIDv7 后写入
非法 X-Trace-ID            → 忽略输入并生成 UUIDv7
```

Root Span：

```python
context.init(trace_id=req_ctx.trace_id)

async with span_context(
    _REQUEST_SPAN_NAME,
    span_kind=SpanKind.SERVER,
) as request_span:
    ...
```

中间件会在 scope 内捕获异常并转换响应，因此异常不会自然进入 Span context manager 的 `__aexit__()`。
捕获异常时应显式调用：

```python
request_span.record_exception(exc)
request_span.set_status(StatusCode.ERROR, type(exc).__name__)
```

non-recording Span 上调用这些方法无副作用。必须确保同一个异常只记录一次。

## 7. logger-span demo 破坏性重构

当前 demo 依赖自研 SpanFrame，必须与核心重构一起修改，不保留旧 wire contract。

删除响应字段：

- `span_seq`
- `parent_span_seq`
- `span_path`
- `root_span_path`

新的 Span 节点至少包含：

```python
class SpanNodeSchema(BaseModel):
    trace_id: str
    span_id: str
    parent_span_id: str | None
    span_name: str
    span_kind: str
    status: str
    elapsed_ms: float
    started_at: datetime
    finished_at: datetime
    error_type: str | None
```

实现要求：

- demo service 在进入 Span 前读取当前父 SpanContext，在进入后读取真实 Trace ID / Span ID。
- demo 展示所需计时留在 service 层，不回填到 `pkg/tracing/span.py`。
- 前端使用 `span_id -> parent_span_id` 建树。
- tracing 关闭时，模拟接口返回明确的“Tracing 未启用”错误，不生成虚假 Span。
- mock trace 必须生成合法且确定性的 Trace ID / Span ID，不能继续使用 seq 模拟身份。

## 8. 分阶段实施

### Phase 0：基线与删除清单

- 记录当前 logger、middleware 和 logger-span demo 测试结果。
- 全仓检索所有旧 SpanFrame 和展示字段消费者。
- 确认当前 staged / unstaged 修改，实施时不覆盖无关用户改动。
- 建立明确删除清单，避免遗留两套 Span 身份。

完成标准：所有受影响文件和旧字段引用均已列出。

### Phase 1：配置、依赖与 runtime

- 修改四个 `configs/.env.*` 文件。
- 在 `internal/config.py` 声明必填配置字段。
- 使用 `uv add` 声明 OTel API / SDK 直接依赖并刷新锁文件。
- 新增 tracing runtime、IdGenerator、幂等初始化和关闭。
- 接入 FastAPI lifespan 和 Celery Worker lifecycle。

完成标准：enabled / disabled runtime 可独立测试，未产生外部网络调用。

### Phase 2：重写 span 门面

- 删除自研 Span 树和 Loguru Span 事件。
- 实现 enabled / disabled 两条 `span_context` 路径。
- 实现 `SpanKind` 和 attributes 参数转发。
- 保证异常和取消不被吞掉。

完成标准：`span.py` 只负责 OTel 门面，不保存自研父子关系或计时信息。

### Phase 3：Logger 与 HTTP Root Span

- Logger patcher 改为读取 OTel SpanContext。
- recorder 严格处理 `X-Trace-ID` 并创建 SERVER Root Span。
- 保证 response header、request context、Root Trace ID 和日志 Trace ID 一致。
- 覆盖中间件捕获异常后的 Span ERROR 状态。

完成标准：一次请求内所有真实 Span 共享同一 Trace ID，父子关系由 SDK 表达。

### Phase 4：demo、schema 和前端

- 破坏性更新 DTO / response schema。
- 重写 demo collector。
- 前端切换到 `span_id / parent_span_id`。
- 删除 seq/path 相关 mock 和展示逻辑。

完成标准：API 和前端中不存在旧 Span 身份字段。

### Phase 5：测试与文档收尾

- 删除旧兼容测试。
- 增加配置开关、真实父子关系、异常、并发和生命周期测试。
- 更新 OpenTelemetry 项目文档，说明当前 X-Trace-ID 入口和后续 W3C 扩展点。
- 运行定向测试、全量测试、Ruff、格式检查、mypy 和最终 diff 审计。

## 9. 测试计划

### 9.1 tracing 关闭

- `span_context` 返回 non-recording Span。
- 不产生 finished SDK Span。
- 不生成自定义 Span 日志。
- 正常返回值和异常传播不变。
- `X-Trace-ID` 仍写入访问日志和响应头。

### 9.2 tracing 开启

- 合法 `X-Trace-ID` 成为 SERVER Root Trace ID。
- Root Span 没有父 Span，Kind 为 `SERVER`。
- 子 Span 默认 Kind 为 `INTERNAL`。
- 子 Span 继承 Trace ID，Parent Span ID 指向真实父 Span。
- sibling Span ID 不同且父 Span 相同。
- AnyIO 和 `asyncio.create_task()` 上下文不串线。
- 异常只记录一次 exception event，状态为 `ERROR`。
- 退出子 Span 后恢复父 Span；退出 Root Span 后没有有效当前 Span。
- 非法或缺失 Context Trace ID 安全回退到 SDK 生成值。

测试使用本地 `TracerProvider + SimpleSpanProcessor + InMemorySpanExporter`，不得连接 Collector。

### 9.3 验证命令

```bash
uv run pytest \
  tests/tracing/test_span.py \
  tests/logger/test_middleware_span.py \
  tests/logger/test_logger.py \
  -q

uv run pytest tests/tracing tests/logger tests/middlewares -q
uv run pytest -q

uv run ruff check pkg/tracing pkg/logger internal/middlewares tests/tracing tests/logger
uv run ruff format --check pkg/tracing pkg/logger internal/middlewares tests/tracing tests/logger
uv run mypy pkg/tracing pkg/logger internal/middlewares/recorder.py

git diff --check
git status --short
```

## 10. 本次不包含

- 不解析或注入 `traceparent` / `tracestate`。
- 不传播 Baggage。
- 不接入 OTLP exporter 或 Collector。
- 不启用 FastAPI、HTTP client 或 Celery 自动 instrumentation。
- 不增加生产采样率配置。
- 不修改数据库 Trace 字段或执行数据迁移。
- 不保留旧 Span API 响应字段或 Loguru Span 事件。

后续接入 OTLP 时再在 `configs/.env.*` 增加 exporter endpoint、protocol、timeout、batch 和 sampler 配置，
不得提前把未使用的配置加入本轮。

## 11. 风险与回滚

- 本次会破坏 logger-span demo 的 API 和前端字段，schema、service、controller、测试和前端必须原子更新。
- OpenTelemetry 全局 Provider 不能被常规重置；初始化所有权和测试隔离必须在实现前确定。
- AgentScope 已间接依赖 OTel，必须验证它没有提前安装全局 Provider。
- 客户端可以重复提交同一个 `X-Trace-ID`；Trace ID 不能用于认证、授权、幂等或安全决策。
- `OTEL_TRACING_ENABLED=false` 是运行时回滚开关。关闭后停止创建新 Span，但保留请求日志和
  `X-Trace-ID` 关联能力。
- 实施应作为一个原子提交完成，避免新旧 Span 身份并存的中间版本进入共享分支。

## 12. 完成标准

- `pkg/tracing/span.py` 中不存在自研 Span 树、Span 身份或手工计时。
- `span_context` 是唯一公共埋点入口，全仓不存在 `with_span` 的定义、导出或调用。
- 全仓不存在 `SpanFrame`、`span_seq`、`parent_span_seq`、`span_path` 的有效代码引用。
- 全仓不存在 `span.start`、`span.end`、`span.error` 兼容日志依赖。
- tracing 关闭时 `span_context` 严格 no-op；开启时创建真实 OTel Span。
- 调用点不直接读取 `settings`，开关判断只在 tracing runtime / `span_context` 内完成。
- `configs/.env.*` 是 OTel 运行配置的唯一维护位置。
- `X-Trace-ID`、request context、SERVER Root Trace ID 和日志 Trace ID 一致。
- demo 使用真实 `span_id / parent_span_id` 建树。
- enabled / disabled、Root / child、异常、并发和生命周期均有自动化测试。
- 没有 OTLP 网络请求。
- 定向测试、Ruff、格式检查、目标 mypy 和 `git diff --check` 均有实际通过记录；全量测试中的外部
  集成失败有明确归因和复现记录。

## 13. 实施与验证记录

实施于 2026-07-18，Phase 0 至 Phase 5 均已落地。当前实现未配置 exporter，不会发起 OTLP 网络请求。

已通过：

- 排除需要真实 OpenAI、Redis/Celery 和 Milvus 的外部集成目录后，pytest 回归为
  `463 passed, 1 skipped`。
- 本次新增和修改文件通过 Ruff check 与 Ruff format check。
- tracing runtime、Span 门面、中间件和 demo Service 的目标 mypy 检查通过。
- logger-span 前端内嵌 JavaScript 通过 Node 语法检查。
- `git diff --check` 通过。

全量 `pytest -q` 的实际结果为 `488 passed, 2 skipped, 16 failed`。16 项失败均来自本地环境未满足的
外部集成条件：OpenAI-compatible API Key 无效 5 项、Celery/Redis 服务不可用 9 项、Milvus 服务不可用
2 项；本轮 OpenTelemetry 相关测试没有失败。
