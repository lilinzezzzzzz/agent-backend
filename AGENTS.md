# AGENTS.md

本文件记录跨目录的项目约束；局部 `AGENTS.md` 只补充所在目录的规则。使用说明、实现细节和验证记录
分别保留在 README、代码/测试和对应文档中，不在规则文件中复制版本号、测试总数或计划进度。

## 项目边界

- 本仓库是面向 Agent、RAG 与 AI 平台场景的 FastAPI demo 和后端脚手架；运行方式与依赖版本以
  `README.md`、`pyproject.toml`、`uv.lock` 和现有 entrypoint 为准。仓库只提供后端，不恢复前端或静态页面托管。
- 实验 API 放在 `examples/api/` 的独立 demo app，不挂回常规入口；生产运行时代码不得依赖 `examples/`。
- 计划状态与历史记录遵循 `plans/AGENTS.md`；已实现但仍有验收缺口的计划不能视为完成。
- `internal/` 承载应用装配和业务实现，可以依赖 `pkg/`；`pkg/` 是可复用基础包，不得反向依赖
  `internal/`。
- 修改现有能力时优先沿用相邻代码的 Controller / Service / DAO / Cache / Schema / Infra 边界，
  不为同一职责新增平行抽象。

## 稳定 Contract

- 认证由 Router 挂载及依赖决定：`/v1` 默认 token，`/v1/public` 匿名，`/v1/internal` 内部签名。
  `/v1/auth` 下有显式匿名入口，不能仅凭路径前缀判断认证；新增例外必须在注册与认证测试中明确。
- HTTP 响应使用现有 `BaseResponse[T]` / `BaseListResponse[T]` 信封和 `pkg.api_response` 工厂；
  `internal.core.errors` 的错误码语义保持稳定。
- 数据库写入通过 Service / DAO 或已有数据库 helper 完成，不在 Controller 中直接操作 session。
- Service 返回给 Router 的轻量 DTO 使用 `*DTO` 命名，放在 `internal/schemas/<domain>.py`；请求和响应
  schema 分别使用 `*ReqSchema`、`*RespSchema` 或现有 detail/item 命名。较大领域可使用同名 package
  按职责分组，并由 package 入口维持稳定的 schema/DTO 导入 contract。
- 缓存 key/TTL、Celery task name/queue、向量 collection schema、公开 API 字段和持久化格式均按兼容性
  contract 处理；修改时同步受影响的实现、测试和必要文档。

## 配置与安全

- `internal/config.py` 是配置加载入口。应用从根 `.env` 或显式注入取得 `APP_ENV`，读取嵌套 TOML
  `configs/app.{APP_ENV}.toml` 和根 `.secrets`；除 `APP_ENV` 外不增加 shell 环境变量兜底。
  `.env` 中的 Compose 部署参数不作为应用配置来源。
- 非敏感运行参数必须在环境配置中声明，凭据只能放在 `.secrets` 允许的字段中；不得提交或记录真实
  secret、token、密码、私钥、数据库连接串或生产配置。
- 根 `Dockerfile`、`compose.yaml`、`.env.compose.example` 与 `deploy/` 共同构成容器部署 contract；
  修改这些资产时同时阅读 `deploy/AGENTS.md`。保持 API 非 root、配置和密钥只读挂载、journald 固定
  服务标签及最小权限边界。
- `configs/` 只保存非敏感运行参数，使用与 `Settings` 分组一致的嵌套 TOML 和原生值；各环境保持同名
  table/字段，用明确值关闭能力，不用缺字段或 JSON-in-string 表达。密钥示例只放 `.secrets.example`。
- 新增配置时同步模型、适用环境文件、provider 和测试；保持 `extra="forbid"`、跨分组约束及默认脱敏，
  不绕过 `Settings.settings_customise_sources()`。`config_echo.reveal_secrets` 默认关闭。
- `entrypoints/` 保持薄层，不复制应用装配、配置加载或业务逻辑，import 时不启动连接、线程或后台任务；
  Shell 入口切到仓库根目录并用 `exec` 启动最终进程。Worker / Beat 的 Celery CLI 直接引用
  `internal.infra.celery.application:celery_app`，与应用内部共用实例；路径、pool、队列及 CLI 参数变更同步部署入口。
- Uvicorn 日志配置与 `internal/infra/logging.py` 配套维护，验证主进程、子进程及 lifespan 前后的输出。

## 数据与验证

- 数据库仅支持 PostgreSQL 与 SQLite；SQLite 用于测试与轻量本地存储，部署、迁移、行锁和强并发
  能力使用 PostgreSQL，不为其他方言增加兼容分支。
- PostgreSQL 建库和升级以 `migrations/versions/` 的 Alembic revision 为准；结构变更同步 ORM 与新增
  revision；不维护建库 SQL 快照，离线 SQL 按需通过 Alembic 生成。初始化以空库为起点，不自动接管已有库，
  不通过 `stamp head` 代替结构核对；迁移作为独立步骤执行，不放入 API / Worker 启动链。
- 已用于共享环境的 revision 不改写，修正通过新增 revision 完成；涉及已有数据的变更必须评估历史数据、
  锁表、部署顺序、兼容窗口和回滚，不能把自动生成或 `alembic check` 通过视为完整正确性证明。
- Python 版本、Ruff 和测试配置以 `pyproject.toml` 为准。运行能覆盖改动行为的最小验证，再按共享 contract
  的影响范围扩大；不得声称未实际运行的检查通过。
- 默认不假设数据库、Redis、Celery Worker、Milvus 或外部模型可用；依赖这些服务的测试标记为
  `integration`，并明确前置条件。测试隔离与安全目标要求见 `tests/AGENTS.md`。

## 示例、脚本与文档

- `examples/` 不作为公共包或生产 entrypoint。示例写明启动方式、外部服务、端口和凭据前置条件，网络服务
  默认绑定 loopback；API demo 复用常规装配与配置链，新增实验路由时验证常规 app 不暴露它。
- 独立 LLM / MCP 示例使用显式凭据输入，不硬编码或回显；工具参数按不可信输入校验，保留未知工具、循环
  上限和错误边界。邮件、支付、删除等外部副作用默认 mock，真实操作需明确开关与用户确认。
- `scripts/` 不作为业务逻辑的唯一实现位置。操作前校验参数、系统与精确目标；删除、覆盖、重建或停止资源
  时拒绝宽泛根目录和默认生产目标，只清理自身创建的隔离资源。外部写入、生产操作和昂贵任务需明确目标与确认。
- 脚本按解释器检查语法，失败返回非零状态并保留脱敏诊断；破坏性路径仅在隔离资源演练。journald 验收脚本
  仅用于获准的本机 Linux Docker 环境，保持独立 Compose 项目和 loopback 端口，不操作已有部署或远程 context。
- `docs/README.md` 维护文档导航；新增、移动或删除文档时同步索引和相对链接。文档区分当前实现、目标设计、
  历史记录与未验证建议，使用真实路径、字段和命令，不增加无人维护的日期元数据。
- 迁移、部署和外部服务命令说明目标及前置条件；历史验证记录保留原提交与环境，不能作为当前版本通过的证据。
  临时产物和保留的证据目录说明用途，不在规则文件中重复验收结果。

## AGENTS.md 维护

- 修改代码、开发功能、重构或调整配置、依赖、测试及部署方式时，检查受影响目录及祖先目录的 `AGENTS.md`；
  按改动范围读取下表对应的跨目录配套规则，无需加载无关文件，确认适用规则与改动后的约束一致。
- 目录职责、分层边界、稳定 contract、配置来源、资源生命周期、数据安全或验证要求变化时，在同一改动中
  同步维护对应规则。移动或删除规则文件时先合并仍有效的约束，再更新引用及适用范围。
- 跨目录约束由根文件管理；局部规则归最近的适用目录。优先合并到现有文件，避免重复父级规则；只有存在
  难以由父级清楚表达、且容易被后续修改违反的独立约束时，才新增局部文件，不按包或目录数量铺设规则。
- `AGENTS.md` 记录持续约束；使用说明、实现细节、临时方案、计划进度和验证记录放对应 README、代码、计划
  或文档中。改动未影响约束时无需修改规则文件；交付前检查准确性、继承关系和路径引用。

| 配套规则 | 适用范围 |
| --- | --- |
| `internal/AGENTS.md` | 应用分层、认证、业务 Agent、RAG、Celery 装配与任务 |
| `pkg/AGENTS.md` | 公共包及其调用方涉及的共享协议、序列化、资源所有权 |
| `internal/services/agents/AGENTS.md` | Agent 会话、确认执行、受管理运行及影响恢复的 builder/工具变更 |
| `tests/AGENTS.md` | 测试、fixture、收集隔离与外部服务验证 |
| `migrations/AGENTS.md` | ORM 结构变更、Alembic revision 与 SQL 导出 |
| `deploy/AGENTS.md` | 容器、部署参数、宿主机日志配置与部署验收 |
| `plans/AGENTS.md` | 实施计划、验收状态及历史记录 |
