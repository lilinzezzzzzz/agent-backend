# OpenTelemetry 上下文传播与 OTLP 上报机制

本文说明 OpenTelemetry 如何把跨端调用关联为同一条 Trace，以及 OTLP 在可观测性数据上报链路中的职责边界。
当前项目的接入方式、实现边界和持久化方案见
[当前项目 OpenTelemetry 接入与可观测性数据持久化](project_integration_and_persistence.md)。

## 1. 什么是 OpenTelemetry

OpenTelemetry（简称 OTel）是一套统一采集和传输可观测性数据的解决方案与标准体系，
为 Trace、Metric 和 Log 的生成、关联、采集与导出提供厂商无关的规范、API、SDK、协议和工具。

### 1.1 OpenTelemetry 解决什么痛点

不同语言、框架和可观测性厂商通常提供各自的埋点 SDK、数据模型和上报协议。
一套分布式系统可能因此维护多种 Agent、Exporter 和字段约定，并在切换后端时重新适配甚至重新埋点。

| 痛点 | 典型表现 | OpenTelemetry 的处理方式 |
| --- | --- | --- |
| 厂商绑定 | 应用直接依赖某个 APM 厂商的私有 SDK 和协议，切换后端需要修改代码 | 提供厂商无关的 API、SDK 和 OTLP，允许同一套埋点对接不同后端 |
| 数据语义不一致 | 不同语言对相同 HTTP、数据库或消息操作使用不同属性名 | 通过 Specification 和 Semantic Conventions 统一概念、名称与含义 |
| 跨服务链路断裂 | 不同框架或追踪厂商无法识别彼此的 Trace 上下文 | 使用标准 Propagator 和 W3C Trace Context 传播调用链上下文 |
| 采集管道重复建设 | 每个服务分别实现批处理、重试、过滤、脱敏和多后端转发 | 使用 SDK Processor、Exporter 和 Collector 复用通用采集与转发能力 |

### 1.2 主要可观测性信号

三类主要可观测性信号分别回答不同问题：

| 信号 | 主要回答的问题 | 典型内容 |
| --- | --- | --- |
| Trace | 一次请求经过了哪些组件，耗时和错误发生在哪里 | Trace、Span、事件和调用关系 |
| Metric | 系统在一段时间内运行得怎么样 | 请求量、延迟、错误率、CPU 和内存 |
| Log | 某个时间点具体发生了什么 | 结构化或非结构化事件记录 |

OpenTelemetry 统一的是这些数据从应用产生到发送给后端之前的标准和工具，
但不要求不同信号存储在同一个后端。

### 1.3 核心组成

| 组成部分 | 职责 |
| --- | --- |
| Specification | 定义跨语言实现需要遵守的行为和数据约定 |
| API | 定义生成和关联可观测性数据的编程接口 |
| SDK | 实现采样、处理、聚合和导出等运行时能力 |
| Instrumentation | 使用 API 对应用、框架和依赖进行手动或自动埋点 |
| Semantic Conventions | 统一常见可观测性属性的名称和语义 |
| Propagator | 在进程和服务之间注入、提取追踪上下文 |
| OTLP | 定义可观测性数据的格式、编码、传输和交付语义 |
| Collector | 接收、处理并转发可观测性数据 |

OpenTelemetry 本身不是可观测性后端，通常不负责可观测性数据的长期存储、查询、仪表盘、告警和最终展示。
这些能力由 Jaeger、Prometheus、Grafana 生态或商业可观测性平台提供。

## 2. 先区分两条数据路径

理解 OpenTelemetry 的关键，是区分“业务请求中的上下文传播”和“可观测性数据上报”。
它们可以共享同一个 `trace-id`，但目的、载体和执行组件不同。

```mermaid
flowchart LR
    Client[浏览器或 App] -->|业务请求 + traceparent| ServiceA[服务 A]
    ServiceA -->|业务请求 + traceparent| ServiceB[服务 B]
    ServiceA -.记录可观测性数据.-> SDK[OpenTelemetry SDK]
    ServiceB -.记录可观测性数据.-> SDK
    SDK --> Exporter[OTLP Exporter]
    Exporter -->|OTLP| Collector[Collector 可选]
    Collector --> Backend[Jaeger / Tempo / 其他后端]
    Exporter -->|OTLP 可直连| Backend
```

| 路径 | 目的 | 常见载体 | 主要执行者 | 标准或协议 |
| --- | --- | --- | --- | --- |
| 上下文传播 | 让下游创建属于同一 Trace 的子 Span | HTTP Header、gRPC metadata、消息属性 | Propagator / Instrumentation | W3C Trace Context 等 |
| 可观测性数据上报 | 把已生成的 Span、Metric、Log 发送给接收端 | 独立的 HTTP 或 gRPC 请求 | Processor / Reader、Exporter、Receiver | OTLP 等 |

> W3C Trace Context 负责把调用链串起来，OTLP 负责把已经采集的可观测性数据报上去。

## 3. W3C Trace Context 如何串联多端

当浏览器、App、网关或服务发起下游请求时，Instrumentation 通常通过 Propagator 将当前 Span 的上下文注入载体；
下游从载体中提取上下文，并以它为父级创建新的 Span。
HTTP 通常使用 Header，gRPC 和消息队列则需要使用各自的 metadata 或消息属性。
如果某种客户端、服务端或消息组件没有对应的自动埋点，就需要手动执行注入和提取。

### 3.1 `traceparent` 与 `tracestate`

OpenTelemetry 默认使用 W3C Trace Context 格式。
HTTP 请求至少传播 `traceparent`；存在厂商扩展状态时，还可以传播可选的 `tracestate`：

```http
traceparent: 00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01
tracestate: vendor_specific_value
```

`traceparent` 的格式为：

```text
<version>-<trace-id>-<parent-id>-<trace-flags>
```

字段含义如下：

| 字段 | 作用 |
| --- | --- |
| `version` | Trace Context 格式版本 |
| `trace-id` | 整条调用链共享的唯一标识 |
| `parent-id` | 当前上游 Span 的标识 |
| `trace-flags` | 固定两个十六进制字符的 8-bit 标志字段 |

这四个字段在 `version=00` 的 `traceparent` 中都不能省略。
`trace-flags` 字段必填，但 sampled 标志不要求必须开启：常见的 `01` 表示 sampled 位为 1，`00` 表示 sampled 位为 0。
它是 bit field，W3C Trace Context Level 2 还定义了值为 `02` 的 random-trace-id 位，因此解析 sampled 状态时应检查最低位，而不是比较整个值是否等于 `01`。
这些标志是上游给出的追踪建议，不能作为可信的安全指令。

同一条 Trace 通常保持相同的 `trace-id`；每个参与追踪的服务创建新的 `span-id`，并将收到的 `parent-id` 作为父级。
后端据此还原 Span 的父子结构。

`tracestate` 不是 `traceparent` 的组成字段，而是独立、可选的 Header，用于传播追踪厂商的扩展状态。
接收方解析失败的 `traceparent` 时，不应继续使用关联的 `tracestate`。

### 3.2 Baggage 完全可选

Baggage 不是实现分布式追踪的必要组件。
不使用 Baggage，仍然可以通过 W3C Trace Context 完整传播 `trace-id` 和父 Span 信息。

它适合传播不属于核心业务契约、缺失时不影响业务正确性的横切上下文，例如：

```http
baggage: experiment.group=checkout-v2,request.source=mobile
```

> 如果接收方缺少这个字段就无法正确完成业务，它应该进入正式接口契约，而不是 Baggage。

业务必需的数据应进入请求体、路径参数、查询参数或明确定义的业务 Header。
Baggage 不会自动成为 Span 属性或通过 OTLP 上报；如需在后端检索，Instrumentation 必须明确将所需值复制为 Span 属性。

来自外部的 Trace Context 和 Baggage 都可能被伪造。
不应使用它们执行认证、授权等安全决策，也不应在 Baggage 中放入密码、Token、API Key 或个人信息。

## 4. OTLP 具体规定什么

OTLP（OpenTelemetry Protocol）是可观测性数据的 wire protocol，规定数据如何编码、传输和交付。
它是协议规范，不是可运行程序，因此不会自行执行上报。

### 4.1 数据模型

OTLP 使用 Protocol Buffers 定义 Trace、Metric 和 Log 的请求、响应和数据结构。
以 Trace 导出请求为例：

```text
ExportTraceServiceRequest
└── ResourceSpans[]
    ├── Resource                 # 产生数据的服务、进程或容器
    └── ScopeSpans[]
        ├── InstrumentationScope # 生成数据的 Instrumentation
        └── Spans[]              # trace_id、span_id、时间、属性和状态等
```

`service.name`、`http.request.method` 等属性的标准名称由 Semantic Conventions 定义；
OTLP 负责承载这些属性，不负责定义其业务语义。

### 4.2 编码与传输

OTLP 定义了 gRPC 和 HTTP 两种主要传输方式：

| 方式 | 编码 | 默认端口 | 默认路径 |
| --- | --- | --- | --- |
| OTLP/gRPC | Protobuf 二进制 | `4317` | gRPC `Export` 方法 |
| OTLP/HTTP | Protobuf 二进制或 Protobuf JSON 映射 | `4318` | 按信号区分 |

OTLP/HTTP 默认使用以下路径：

```http
POST /v1/traces
POST /v1/metrics
POST /v1/logs
```

JSON 形式仍然必须遵守 OTLP Protobuf schema，并不是可以任意组织字段的普通 JSON。

### 4.3 谁真正执行上报

实际执行网络上报的是 OTLP Exporter：

```mermaid
flowchart LR
    SDK[OpenTelemetry SDK] --> Processor[Processor / Reader]
    Processor --> Exporter[OTLP Exporter]
    Exporter -->|OTLP| Receiver[Collector 或后端的 OTLP Receiver]
```

> OpenTelemetry SDK 中的 OTLP Exporter 按照 OTLP 规范，将可观测性数据发送给 Collector 或可观测性后端。

以 Trace 为例，至少需要正确组装：

```text
TracerProvider
  + SpanProcessor
  + OTLPSpanExporter
  + 可访问的 Collector 或后端地址
```

自动埋点发行版可能会自动组装这些组件，但真正执行网络请求的仍然是 Exporter 实现。

Collector 不是使用 OTLP 的必要条件。
应用可以直接向支持 OTLP 的后端发送数据；引入 Collector 后，可以让它承担额外的批处理、过滤、脱敏、路由、协议转换和多后端转发：

```text
应用 ──OTLP──> 可观测性后端

应用 ──OTLP──> Collector ──OTLP 或其他协议──> 可观测性后端
```

### 4.4 交付语义与边界

OTLP 还定义请求与响应、压缩、完全成功、部分成功和可重试错误等行为。
OTLP/HTTP 的完全成功和部分成功都返回 `200 OK`，部分成功通过响应中的 `partial_success` 表达；客户端不应重试已返回的部分成功请求。

OTLP 只关注相邻客户端和服务端之间的交付，不提供应用到最终存储端的端到端持久化保证。
Exporter、Collector 和后端仍需根据部署方式配置队列、超时、重试和持久化策略。

## 5. 可观测性数据如何持久化

OpenTelemetry 负责生成、处理和传输可观测性数据，但不定义通用的长期存储系统。
数据持久化由接收 OTLP 的可观测性后端，或 Collector 下游连接的存储系统完成。

### 5.1 组件职责边界

| 组件 | 持久化链路中的职责 | 通常不负责 |
| --- | --- | --- |
| API / SDK | 生成数据，执行采样、聚合和批处理 | 长期存储和查询 |
| Exporter | 按 OTLP 或其他协议发送数据 | 判断最终存储是否可查询 |
| Collector | 接收、处理、过滤、脱敏、路由和转发数据 | 默认提供完整的长期存储与查询能力 |
| 可观测性后端 | 写入存储、建立索引并提供查询、仪表盘和告警 | 业务请求中的上下文传播 |
| 底层存储 | 保存数据副本并执行索引、保留和删除策略 | 理解业务调用链语义，除非上层完成数据建模 |

Collector 可以使用内存或磁盘队列缓冲待导出的数据，但缓冲队列不是最终可观测性存储。
它解决的是短暂故障期间的传输可靠性；长期查询、保留周期、索引和访问控制仍由后端负责。

### 5.2 典型持久化链路

```mermaid
flowchart LR
    App[应用] --> SDK[OpenTelemetry SDK]
    SDK --> Exporter[Exporter]
    Exporter -->|OTLP| Receiver[Collector 或后端 Receiver]
    Receiver --> TracePipeline[Trace pipeline]
    Receiver --> MetricPipeline[Metric pipeline]
    Receiver --> LogPipeline[Log pipeline]
    TracePipeline --> TraceStore[Trace 存储]
    MetricPipeline --> MetricStore[Metric 时序存储]
    LogPipeline --> LogStore[Log 存储]
    TraceStore --> Query[查询 / 仪表盘 / 告警]
    MetricStore --> Query
    LogStore --> Query
```

同一套 Collector 可以把不同信号路由到不同后端。即使 Trace、Metric 和 Log 分别存储，也可以通过
`trace_id`、`span_id`、`service.name`、时间戳和 Resource 属性建立关联。

OTLP 是传输协议，不是数据库存储格式。后端通常会把 OTLP 数据转换成自己的表结构、索引或时序模型：

| 信号 | 持久化时关注的数据关系 | 典型查询 |
| --- | --- | --- |
| Trace | `trace_id`、Span 父子关系、起止时间、状态、属性、事件和 Link | 按 Trace ID 还原调用树，按服务、操作或错误筛选慢请求 |
| Metric | 时间序列、属性维度、数据点、聚合时间语义和直方图 | 按时间窗口聚合请求量、延迟、错误率和资源使用率 |
| Log | 时间、严重级别、正文、结构化字段以及 Trace / Span 关联字段 | 按关键词、字段、时间范围或 Trace ID 检索事件 |

后端选型和数据模型需要匹配主要查询方式。能够接收 OTLP 只说明后端具备协议入口，不自动代表它已经提供合适的
索引、查询 API、仪表盘、告警、数据迁移和容量治理能力。

### 5.3 从“已生成”到“可查询”

一条数据通常依次经历以下状态：

```text
SDK 已生成
  → Processor / Reader 已接收
  → Exporter 已发送
  → 相邻 Receiver 已接受
  → 后端已写入
  → 索引或聚合已可查询
```

这些状态不能相互替代：

- Span 已结束，不代表 Batch Processor 已完成导出。
- OTLP 返回完全成功，只能确认相邻 Receiver 已接受数据，不代表最终存储已经写入。
- Collector 已转发，不代表后端索引立即可见。
- 查询暂时未命中，可能是数据丢失，也可能是批处理或索引的最终一致性延迟。
- 查询已经命中，不代表数据会永久保留；后端仍可能按 TTL 或保留策略删除它。

普通业务请求通常不应同步等待最终持久化，否则可观测性后端会成为业务可用性的强依赖。
需要通过 Collector 与后端指标、队列状态、导出错误和端到端探针监控数据缺口。

### 5.4 持久化设计需要明确什么

- **查询路径**：按 Trace ID、时间范围、服务、操作、错误状态、日志字段或 Metric 维度查询。
- **索引与基数**：限制动态 Span 名称和高基数属性，评估索引、内存和存储成本。
- **保留策略**：为原始数据、聚合数据和不同环境分别设置 TTL、归档与删除规则。
- **容量控制**：明确采样率、聚合方式、单条数据大小、队列上限和峰值写入能力。
- **可靠性**：配置有界队列、超时、重试、持久缓冲、优雅关闭以及后端不可用时的降级行为。
- **数据安全**：采集前过滤凭证和个人信息，传输使用 TLS 与认证，查询执行最小权限和租户隔离。
- **数据治理**：定义 schema 演进、后端升级、备份恢复、审计、迁移和回滚方式。
- **端到端验收**：使用已知 Trace 或测试数据验证接收、写入、查询、关联、延迟和过期删除行为。

具体项目应在独立文档中记录当前实现、目标后端、部署拓扑和验收状态；本项目见
[当前项目 OpenTelemetry 接入与可观测性数据持久化](project_integration_and_persistence.md)。

## 6. 工程落地检查表

- 所有需要串联的入口和下游客户端都已启用 Instrumentation。
- HTTP、gRPC 和消息队列分别在正确载体中注入、提取 Trace Context。
- 浏览器跨域请求已允许所需的 `traceparent`、`tracestate` 和 `baggage` Header。
- 不信任外部传入的 Trace Context，不在 Baggage、Span 属性或日志中记录凭证和敏感个人数据。
- SDK 已配置对应信号的 Processor / Reader、OTLP Exporter 和可访问的接收端。
- OTLP endpoint、传输方式、端口、TLS、认证及信号路径相互匹配。
- 若使用 Collector，OTLP Receiver 和各信号 pipeline 均已配置；若直连后端，后端明确支持所选 OTLP 方式。
- 批处理、超时、队列、重试和进程关闭时的 flush 行为符合可靠性要求。
- 采样策略可解释，并能区分“上下文已传播”和“Span 实际已记录并上报”。
- 使用同一 `trace-id` 验证上下游 Span 是否在后端正确关联。
- 后端的索引、保留周期、TTL、容量、查询权限和敏感字段治理已经明确。
- 已区分“Receiver 接受”“后端写入”和“查询可见”，并对持久化延迟、失败和数据丢弃建立监控。

## 7. 参考资料

- [OpenTelemetry：What is OpenTelemetry?](https://opentelemetry.io/docs/what-is-opentelemetry/)
- [OpenTelemetry：Components](https://opentelemetry.io/docs/concepts/components/)
- [OpenTelemetry：Context propagation](https://opentelemetry.io/docs/concepts/context-propagation/)
- [W3C Trace Context Level 2（Candidate Recommendation Draft）](https://www.w3.org/TR/trace-context-2/)
- [W3C Baggage](https://www.w3.org/TR/baggage/)
- [OpenTelemetry：OTLP Specification](https://opentelemetry.io/docs/specs/otlp/)
- [OpenTelemetry Collector](https://opentelemetry.io/docs/collector/)
- [OpenTelemetry Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/)
