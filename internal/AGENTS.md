# AGENTS.md

`internal/` 承载应用装配和业务实现。本文件集中管理认证、业务 Agent、RAG 与任务的局部约束；
会话、确认执行及受管理运行另见 `internal/services/agents/AGENTS.md`。

## 分层边界

- Controller：声明路由、认证依赖、请求/响应 schema 和响应组装；不直接查询数据库、操作缓存或编排外部服务。
- Service：承载业务规则、权限、幂等、事务以及 DAO / Cache / 外部能力编排；不构造 HTTP 响应信封。
- DAO / Cache：分别封装持久化访问和业务缓存语义，不承担业务状态判断；批量场景使用批量查询和写入，
  避免循环 I/O。
- Schema / DTO：承载传输校验、wire enum 和纯字段映射，不访问数据库、缓存、上下文、配置或外部服务。
- Model：只描述 ORM 映射和持久化字段；字段、索引、约束及默认值与 Alembic 迁移保持一致，
  新模型在 `internal/models/__init__.py` 注册。
- Infra：拥有数据库、Redis、LLM、Celery 和调度器等连接生命周期；业务层不自行创建重复连接池。
- Middleware：处理请求上下文、日志、guard 和异常转换；token 与内部签名认证仍由
  `internal/dependencies/auth.py` 按 Router 边界执行。
- Task：复用 Service / DAO，不复制 Controller 逻辑；按 at-least-once 语义设计并保持 task name 稳定。

## 应用与 API Contract

- API 资源由 `internal.app.lifespan` 编排；Worker 子进程和任务级资源遵循 `internal/infra/celery/` 的
  生命周期，不复用 API 的 event loop 或连接。模块 import 时不得连接外部服务或启动后台任务。
- Router 按 `/v1`、`/v1/public`、`/v1/internal` 认证边界挂载，正常响应使用现有 response model 和
  `pkg.api_response` payload 工厂，业务错误抛 `AppException`。
- 仅在认证、资源归属、副作用、幂等或特殊错误语义无法从签名和 schema 看出时补充 Router docstring；
  不要求固定章节模板。
- Service、DAO 和 Cache 优先沿用现有 `new_*()` factory。只有生命周期与测试隔离允许时才复用模块级实例，
  不把单例作为新增实现的硬性要求。
- 新增缓存 factory 时同步 `internal/factory_cache.py` 的清理注册；reset factory 只清理引用，不能代替
  连接、日志或 tracing 的关闭。
- 错误码、HTTP 状态映射、公开字段、DTO 映射、缓存 key/TTL 和任务参数属于兼容性边界。

## 数据、配置与生命周期

- `Settings` 字段必须明确属于非敏感环境配置或 `.secrets`，并同步适用环境文件、provider 和测试；
  保持 `extra="forbid"` 及日志脱敏。
- 调用方持有数据库 session 时，DAO 不得擅自 commit、rollback、关闭或切换连接；跨语句原子写入由
  Service 确定事务边界。
- 初始化和清理应可重复调用；取消、连接失败和部分启动失败不能被静默吞掉。
- 日志和错误信息不得包含密码、secret、完整 token、授权 code、连接 URI 或未脱敏业务 payload。

## 认证依赖

- `internal/dependencies/` 只声明认证依赖和 guard；Service 依赖在调用方直接声明，不在这里建立 alias。
  认证 alias 优先使用 `Depends(...)`，只有确需同时表达类型与依赖元数据时才用 typed `Annotated`；
  Header/Query/Path/Body 元数据不受此偏好限制。复用既有 `*Dep` / `*Dependency` 名称，不新增平行别名。
- Token 认证建立 `AuthenticatedPrincipal` 并写入用户上下文；内部签名不建立登录用户，不能代替资源归属校验。
  修改时保留 Authorization 解析与稳定错误码，验证 Router 实际挂载、匿名例外和失败分支。

## 业务 Agent

- `internal/agents/` 只实现 prompt、builder、router 和工具适配，业务用例归 Service，通用执行循环归
  `pkg/agents/`。Builder 只组装注入的 action maker、工具及步数上限，不创建连接、全局业务状态或恢复状态机。
- 业务定义与工具解析沿用 `internal/agents/registry.py`；影响恢复的 prompt、工具或参数变化需评估
  definition version，并同步 Service、API、领域 README 和测试。
- Prompt 不作为权限、幂等、事务、金额或状态机规则的唯一执行者；handler 只做轻量适配，业务校验和提交
  由 Service / Workflow 强制执行。Router 只决定业务域，无法可靠路由时显式 unsupported，不代答专业问题。
- ReAct 只准备可丢弃的 pending action；真实业务副作用经确定性确认路径绕过 LLM，以可信状态、用户绑定、
  幂等、事务和必要补偿保护。工具不直接操作 ORM、Redis、Celery、支付渠道或真实开票系统。
- 工具显式声明 `ToolReplayPolicy`；只读/纯计算可用 `replay_safe`，随机 pending token 动作保持
  `non_replayable`。`prepare_invoice_request` 不得通过重调生成新 token 来替代原动作。
- 订单 Agent 保持订单、物流、售后、退款和发票边界，越界请求交统一 router；订单归属在 Service 校验。
  支付 Agent 保持只读/纯计算，规则与知识经工具获取，缺参或工具失败不猜测结果，新增工具重新判断重放策略。
- 订单与支付金额使用整数分，由确定性工具计算；支付输入非负，优惠不得超过商品加运费，不以 float 或 LLM
  输出作为权威值。真实支付、扣款、退款、解绑或账单修改需要独立 Workflow、回调验签和补偿。

## RAG

- `internal/rag/` 只映射业务实体与通用向量 contract；授权范围及回答编排归 `RagService`，SDK 细节归
  `pkg/vectors/backends/milvus/`，不在业务 repository 导入 `pymilvus`、复制 filter/retry 或重复计算 embedding。
- 用户、租户、domain、kb_id 与工具传入的 `kb_ids` 不是授权依据；由可信业务上下文在检索前限制范围。
  检索文本作为资料，不提升为执行指令，也不依靠召回后提示模型忽略越权内容。
- `doc_id`、`kb_id`、`domain`、`chunk_index`、主键、正文及 embedding dimension 是持久化 contract；
  模型或维度变化评估存量向量兼容性。`ensure_collection()` 只创建/校验，不静默重建或迁移不兼容 collection。
- Milvus / Embedding 失败不能伪装为空召回，由 Service 转为稳定错误或显式降级；召回、重排及上下文预算
  变化保留来源与排序，验证过滤后结果及空召回。

## Celery 装配与任务

- `internal/infra/celery/application.py` 唯一创建应用 `celery_client` / `celery_app` 并注册 Worker hook；
  包 `__init__.py` 不实例化或 re-export runtime 对象，调用方从职责模块显式导入。
- `lifecycle.py` 管理子进程配置、日志与 tracing；`execution.py` 用既有 `run_in_async()` 管理任务级 loop、
  DB、Redis 和 request context，资源不跨 prefork 或多次执行复用，失败/取消也清理。退出前清理 tracing 并 drain 日志。
- `health.py` 只检查 API 使用的 broker 健康，失败不隐式改变 Worker 或业务任务状态。
- 任务实现、共享标识及 timeout 归 `internal/tasks/` 与其中的 `constants.py`；include、route、Beat schedule
  统一归 `internal/tasks/scheduler.py`，默认 Worker 队列从 `get_worker_queue_names()` 派生，不维护平行清单。
- task name、queue、参数和 trace header 是跨进程 contract；修改时检查完整投递链。数据库状态是业务任务
  权威，broker 投递/撤销成功不等于业务完成/取消；保留幂等请求比较、execution token fencing 和已推进状态。
- PostgreSQL 幂等与 reconciler 能力受配置开关约束，SQLite 明确禁用；reconciler 按既有状态、期限、fence
  和批量上限推进，不盲目重发可能已执行的副作用。旧 execution token 不得提交终态。
