# AGENTS.md

`pkg/` 提供可复用基础能力，不得依赖 `internal/`、应用全局配置或具体业务错误响应。各主题包的持续约束
集中在本文件，按修改范围阅读对应章节；使用方法见对应 README，具体 API 以实现和测试为准。

## 共享边界

- 公共符号、参数、返回值、异常类型、序列化格式和 re-export 可能被多处依赖；破坏性调整前检索调用方，
  并在同一改动中更新它们。
- 配置、凭据、logger、连接和业务上下文通过参数或 provider 注入；模块 import 时不执行网络、文件、数据库
  或日志 I/O。
- 公共包的延迟导入或 re-export 保持真实模块身份；测试不得通过全局替换模块来掩盖包入口或依赖方向问题。
- 异步 API 不引入未隔离的同步阻塞调用；取消、超时、关闭和部分失败语义保持显式。
- 错误保留可诊断 cause，但不得绑定 HTTP response 或泄露 secret、token、连接串和敏感 payload。
- 新能力优先放入职责匹配的现有主题包；普通纯工具无需为目录结构设置任意行数阈值。
- 包级规则只记录独立约束；用法与实现选择放对应 README，不为每个小工具新增重复根规则的 `AGENTS.md`。
- 修改共享能力时运行该包及行为不同的代表性调用方的最小测试。

## Agent、LLM 与 Embedding

- `agents/` 使用结构化 `AgentToolCall` / `AgentFinal`，工具显式声明名称、参数 schema、handler 和重放策略；
  runner 不解析自然语言动作协议，不直接依赖 provider、数据库或 Redis，外部能力由协议及 handler 注入。
- 执行循环保留步数上限；工具错误按策略处理，非法 action 与未知编程错误不静默吞掉。action、状态、枚举、
  checkpoint 版本、阶段、步骤连续性及 JSON 格式是恢复 contract，无效载荷明确拒绝，不自动修补后执行。
- `AgentRunRuntime` 实现负责归属、持久化及 fencing/revision；执行器依协议提交安全点。`ToolReplayPolicy`
  默认 `non_replayable`，`idempotent` 工具通过稳定调用键去重，未知结果不能视为未执行。循环变更验证完成、
  工具错误、非法动作及步数上限；恢复变更另验证安全暂停、不可重放、lease 丢失及无效 checkpoint。
- `llm/` 的 Responses、Chat Completions 与 Embeddings 独立适配，不做跨 API fallback；共享基类只管理连接
  配置与生命周期。自建 SDK client 由本包关闭，注入 client 由调用方关闭；流提前退出需关闭生成器。
- Responses 保留原生 output、usage、工具调用及事件；失败、未完成、拒绝、空输出、校验失败或缺失流终止
  事件不能伪装成完成。结构化模式按服务能力与显式配置选择，不静默换格式重试；保留本地 Pydantic 校验，
  非 strict schema 不描述为服务端严格保证，provider 差异集中在既有策略位置。
- 审计 hook 在业务解析前接收原始响应，由调用方递归脱敏；不新增明文 prompt、凭据或完整响应日志。
  SDK 替身验证请求、失败流及连接所有权，真实 provider 兼容性只由显式 integration 验证。
- `embeddings/` 管理接口和 registry，OpenAI 实现在 `llm/openai_client/embeddings.py`，不新增平行 SDK
  适配。凭据、URL、模型、dimension 和 timeout 显式注入；空批次不联网，保持顺序及数量/index/指定维度校验。
- Embedding timeout、provider 与响应校验错误保持可区分，取消传播；`*_safe` 不吞异常或返回假向量。
  批量、token 和重试有界，异步热路径不调用同步 SDK；模型/维度变化同步配置及存量 collection 兼容性评估。

## 向量检索

- `vectors/` 的通用类型和 repository 不泄漏 Milvus SDK 类型，阻塞调用走既有 offload；ids、metadata、filter、
  score、模式和排序保持一致。schema、metadata 编码、filter 以及 top_k/candidate_top_k/ranker 默认值按兼容性处理。
- Milvus 的连接/读写归 `vectors/backends/milvus/backend.py`，schema/index 归同目录 `schema.py`，
  row/hit/filter 转换归 `codec.py`；filter 从结构化 operator 转换，不拼接不可信表达式。
  dense、full-text、hybrid 分别校验输入，区分原始 score 与相关度。
- `ensure_collection()` 只幂等创建或校验，不迁移或自动删表重建；字段、dimension、metric、index、BM25 和
  consistency 是持久化 contract。连接仅重试明确可恢复的有界错误，写入重试考虑幂等，取消向上传播。
- 去重、文档折叠、重排及上下文组装保留排序和来源；字符预算不等于 token 预算，组装器不补查相邻 chunk 或
  重新召回。不记录 token、向量、正文或批量 payload，真实 schema/检索只在显式 Milvus 集成环境验证。

## 数据库与 Redis

- `database/` 使用 SQLAlchemy 2.x typed API；statement factory 只组合 SQL。实体查询沿用 `fetch_*`，分页
  提供稳定唯一排序，复杂 join/聚合显式提供与列表语义一致的 `count_statement`；批量访问避免逐条 I/O。
- `update_stmt()` 拒绝未知/受管理字段并要求显式业务条件；调用方提供 session 时 helper 不 commit、rollback、
  关闭或切换它，跨语句原子性由同一事务 session 保证。审计 actor、软删除与分页兼容 PostgreSQL/SQLite。
- SQLAlchemy `URL` 直接交给 engine，避免字符串往返改变密码或 SQLite 文件名；路径回归实际写入、关闭、重开
  并核对文件。SQLite 不模拟行锁/强并发成功；JSON 使用 JSONB/JSON，无其他方言的 TEXT/CLOB 降级。
- 保留 SQL NULL、JSON null、Mutable 顶层追踪及 SQLite 历史双重编码读取；已删除基础 API 不建兼容层，
  破坏性调整一次更新仓库调用方与测试。
- `redis/` 由 provider 注入连接，不隐藏建池或承载业务 key 命名；missing、bytes、JSON 和返回类型保持兼容，
  异常转为 `RedisOperationError` 并保留 cause，不含 value 或凭据。批量空输入不访问 Redis，非空使用批量命令。
- Redis 锁用 `SET NX PX` 和 owner-token 原子 compare-and-delete；TTL/等待/重试为正且有界，取消停止重试。
  过期/未持有不能删除他人的锁，也不表示业务完成或回滚；关键写入仍需数据库约束或 fencing，不承诺 exactly-once。

## 并发、HTTP 与调度

- `concurrency/` 以 AnyIO 为主要抽象，不自建 event loop 或隐藏线程/进程池生命周期；阻塞函数显式 offload，
  fire-and-forget 消费异常，并发额度、取消、timeout 与 shutdown 可观察。
- `AnyioTaskHandler` 线程 timeout 只结束等待，已开始工作继续占用额度直至结束，未开始即超时的工作不得稍后
  执行；资源须保持到线程完成。单次、批量和基础 `anyio_run_in_thread()` 的语义分别对照 README 及并发测试。
- `http_client/` 显式管理 client/流所有权，提前退出释放响应，取消不转正常结果。保留普通请求 `RequestResult`、
  流式异常及按需 `json()` 解析的区别，不把失败流转为正常 EOF；下载流式写入并明确部分文件处理。
- HTTP 的 TLS verify、timeout、base URL 和 header 由构造参数提供，不为测试关闭证书校验，不默认重试非幂等
  请求；网络、HTTP、解析与取消分别验证，日志不含 Authorization、Cookie、query token、body 或敏感 URL。
- `celery_queue/` 只封装 client 和通用 Worker signal，broker/backend/include/route/queue/callback 由外部注入；
  包入口不创建或 re-export runtime，`celery_queue/client.py` 管投递/查询/撤销，`celery_queue/lifecycle.py` 管 hook。
- Celery task_id、headers 和 trace_id 透传不覆盖调用方 header；异步入口 offload 同步 API，传播取消/timeout。
  hook 用稳定 `dispatch_uid` 防重复注册并完成退出清理；投递不代表业务完成，`revoke(terminate=True)` 不作默认取消。
- `scheduler/` 是进程内调度，start/shutdown 幂等，pending job 成功启动后加载；job ID、replace、coalesce、
  max instances、misfire、jitter 及 shutdown 等待语义变更需检查调用方，callback 错误可观察且参数脱敏。
  重复/补跑由业务幂等保护，单进程限制不保证多 worker 只执行一次，跨进程协调沿用业务机制或 Celery Beat。

## 日志、Tracing 与上下文

- `logger/` 保留 lazy proxy，API lifespan/Worker hook 初始化，Uvicorn 启停同步输出归应用适配层；格式与 sink
  集中在 `LoggerHandler`，JSON 用原生 serialize，不二次包装或依赖 TEXT 私有字段，logging 转发用 `InterceptHandler`。
- 日志路径、初始化签名和输出目标是共享 contract；进程退出前 drain 队列，rotation/retention/compression 交部署层。
  测试替换代理目标并清理 sink，不替换模块。
- `tracing/` 不依赖 logger；调用点统一 `async with span_context(...)`，不自行判断开关或模拟 Span。
  保留合法请求 trace ID、W3C `traceparent`、公共 API 与 instrumentation scope；禁用采集仍透传 trace context。
- Tracing init/shutdown 幂等，只关闭自有 provider，不接管或关闭注入 provider；变更验证 API、Worker、异步
  task 切换及禁用场景。不恢复 Span viewer、本地 Span 持久化链或 HTML 托管，OTLP 输出与持久化验收分开报告。
- `request_context/` 的 Web 上下文由最外层 recorder 初始化/清理，Worker 显式管理；未初始化写入失败，
  `get_val()` 允许默认值，缺必需身份的 `get_user_id()` / `get_trace_id()` 抛 `LookupError`，不伪造身份。
- 公共 key 加入 `ContextKey` 并明确类型、owner、清理及传播策略；`scoped_values()` 临时覆盖后恢复，task
  继承不等于 dict 深拷贝，不跨请求共享 mutable 数据，不存 token、密码或大对象。验证并发隔离、继承和清理。

## 安全、身份与配置工具

- `security/` 只提供密码、JWT 与 HMAC 原语，参数由调用方注入；慢哈希 offload，JWT 显式算法 allowlist
  拒绝 none，保留 claims/过期/Bearer 语义。签名生成验证共用 canonicalization、字段、时间单位及 tolerance，恒时比较。
- 不降低安全参数或引入弱算法默认值；历史算法移除需迁移窗口。密码统一在 `security/hasher.py`，
  `needs_rehash()` 只判断格式/成本，业务在密码验证后控制升级；保留错误 hash/空密码语义，不新增平行实现。
- `crypter/` 算法沿用 `BaseCryptoUtil`、`EncryptionAlgorithm`、`register_algorithm()` 及 `get_crypto_class()`；
  `AESCipher` 保持 Fernet 密钥/token 格式及 `ENC(...)` 解密兼容。更换密文/派生/模式需旧密文兼容或迁移，
  验证往返、错密钥、损坏和边界长度。
- `identity_provider/` 显式注入凭据与可关闭 HTTP client，结果归一为 `ExternalIdentityProfile`；新增平台同步
  枚举、配置、实现及 registry，枚举存在不等于已实现，未注册明确失败不回退其他平台。账号绑定冲突归业务 Service。
  `IdentityProviderType` 与身份资料字段按持久化 contract 处理，改名/删除评估数据迁移，测试不访问真实账号。
- `config_loader/` 不作为应用配置旁路；YAML 只用 safe loader，各格式的非 mapping、缺文件、不支持格式按
  既有语义处理，不泛化 YAML 的空值行为。deep merge 只递归 dict、后值覆盖、不拼接 list，保留 ENV 引号/注释/首个等号语义。
- 所有安全与配置工具的日志/异常都不输出明文、密钥、hash 细节、token、签名、敏感待签名内容或中间缓冲；
  保留可识别异常类型，不依赖错误文案作程序判断。

## 响应、类型、ID 与匹配数据

- `api_response/` 保持 `code/message/data` 及 data 内的 `items/page/limit/total`，不提升分页字段至顶层。
  正常响应由 `ResponsePayload` 和调用方 response model 完成校验，直接 `JSONResponse` 留给异常等直接响应路径；
  成功数据限明确支持类型，不隐式序列化 ORM/任意对象，构造载荷不代替最终校验。SSE block 以空行结束，事件名/
  JSON 不引入未转义换行或旁路错误结构，错误不泄露凭据、堆栈或敏感 payload。
- `smart_types/` 的输入、Python 类型、JSON 与 OpenAPI 一致；`SmartInt` 保持正整数约束及超 JS safe integer
  时的字符串输出，`IntStr` 保留字符串输入而不收紧为纯数字。bool 不作 int，不静默取整；Decimal 保精度，
  `SmartDatetime` 保持 naive UTC 与 UTC ISO 输出，验证边界、精度、时区/DST、非法输入及 JSON Schema。
- `ids/` 沿用 `uuid.uuid7()` 完整 128 位及 32 位小写 hex 格式，不截短、不另编码；Trace ID 归 W3C/telemetry。
  变更检查字段、索引和唯一性，ID 不作认证 secret，排序不证明跨进程因果或事务提交顺序。
- `chars_matcher/` 拼音/字形管线分离，新维度独立 matcher 与数据命名空间，共享类型归 `chars_matcher/types.py`；数据懒加载/
  异步预加载，不在 import 阻塞 I/O。拼音批量数据用 `chars_matcher/scripts/rebuild_chars_matcher_pinyin_mappings.py`
  生成，`chars_matcher/chars/shape/name_shape_chars.json` 独立维护，不误用拼音生成器。
- 匹配保留原字置顶、字形 boost、其余拼音原序、去重及单字/复姓区别；候选不等同正确身份。加载与格式变更
  验证首次加载竞态、并发预加载与输入校验。
