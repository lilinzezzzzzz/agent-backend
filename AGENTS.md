# AGENTS.md

本文件适用于整个仓库，是面向 coding agent 的**项目特定约束**。README 面向人类读者，
负责项目介绍、能力清单、快速启动、Docker 和常用命令；本文件只保留会影响代码修改安全性、
兼容性和分层一致性的规则。

## 规则优先级与冲突处理

- 不得仅因不同层级或作用域的 `AGENTS.md` 内容不一致而暂停或阻塞实施。依次遵循平台与安全约束、
  用户最新明确请求、目标文件最近的 `AGENTS.md`、上层 `AGENTS.md`；该顺序足以裁决时继续实施，
  并在交付中说明冲突及需要同步维护的 `AGENTS.md`。
- 只有冲突会实质影响正确性、安全性、数据、公共 contract、兼容性、成本或外部状态，且按上述顺序仍
  无法安全裁决时，才暂停相关部分并请求用户确认；不受冲突影响的部分继续推进。

## 项目定位

- 这是面向 Agent、RAG 与 AI 平台场景的 FastAPI demo 合集与后端脚手架，不是长期承载真实业务流量的
  单一业务服务。
- 主要技术栈和可运行命令以 `README.md`、`pyproject.toml` 和现有 entrypoint 为准，不在本文件重复维护。
- 修改前优先复用已有 service / DAO / schema / toolkit / middleware / infra provider，不新增并行抽象。

## 关键目录索引

- `entrypoints/`：API、Celery Worker / Beat 等进程入口；见 `entrypoints/AGENTS.md`。
- `internal/`：应用装配与业务代码；依赖方向、生命周期和配置规则见 `internal/AGENTS.md`，分层细则见对应
  子目录 `AGENTS.md`。
- `pkg/`：可复用基础包，不应绑定具体业务；数据库、日志、向量、Agent、OSS、第三方登录等细则见
  对应子目录
  `AGENTS.md` 或 `README.md`。
- `configs/`：非敏感环境配置；见 `configs/AGENTS.md`。
- `ddl/`、`dml/`：数据库 schema 与数据脚本；分别见 `ddl/AGENTS.md`、`dml/AGENTS.md`。
- `deploy/`、`scripts/`：部署资产与安全验证脚本；分别见 `deploy/AGENTS.md`、`scripts/AGENTS.md`。
- `examples/`、`frontend/`：可运行示例与 demo 前端；分别见 `examples/AGENTS.md`、`frontend/AGENTS.md`。
- `docs/`：主题文档和计划归档；见 `docs/AGENTS.md`。
- `tests/`：pytest 测试；测试约束见 `tests/AGENTS.md`。

## 分层命名规则

跨 Controller / Service / DTO / DAO / Model / Schema 的通用命名在根目录统一定义，子目录
`AGENTS.md` 只补充该层特有约束。

- `{Resource}`：ORM / domain entity，映射数据库表和持久化字段，例如 `Account`、`Order`、`KnowledgeBase`。
- `{Resource}Dao`：持久化访问对象，封装对应 ORM model 的查询和写入，例如 `AccountDao`、`OrderDao`。
- `{Resource}Service`：业务用例入口，承载领域规则、跨 DAO / 缓存 / 外部服务编排，例如
  `AccountService`、`OrderService`。
- `{Action}DTO` 或 `{Resource}{Action}DTO`：Service 直接返回给 Router 并用于响应映射的轻量数据；
  优先使用 dataclass 定义，命名使用 `*DTO` 后缀，统一放在 `internal/schemas/<domain>.py`，例如
  `LoginDTO`、`AccountLoginDTO`、`OrderSummaryDTO`。DTO 只能做数据承载和纯字段映射，不访问数据库、
  缓存、上下文或外部服务。
- `{Action}ReqSchema` 或 `{Resource}{Action}ReqSchema`：API request schema，用于请求体、查询参数或路径参数的
  传输层校验，例如 `LoginReqSchema`、`AccountLoginReqSchema`。
- `{Action}RespSchema` 或 `{Resource}{Action}RespSchema`：API response schema，用于 `response_model`、响应数据结构和
  OpenAPI contract，例如 `LoginRespSchema`、`AccountLoginRespSchema`。
- `{Resource}DetailSchema` / `{Resource}ItemSchema`：API detail / item schema，用于响应中的资源详情或列表项，例如
  `AccountDetailSchema`、`OrderItemSchema`。

## 兼容性 Contract

修改前确认是否涉及以下项目级 contract；涉及时同步代码、测试、文档、配置、DDL/DML 或迁移说明。

- 路由前缀：`/v1` 默认 token 认证，`/v1/public` 默认无认证，`/v1/internal` 默认签名认证。
- 响应信封：Controller 响应约定和 `BaseResponse[T]` 见 `internal/controllers/AGENTS.md`。
- 错误码：保持 `internal.core.errors` 的稳定语义。
- 认证与签名：主要实现位于 `internal/middlewares/auth.py`、`internal/services/auth.py`、`internal/controllers/api/auth.py`。
- 配置加载：由 `internal/config.py` 管理；`APP_ENV` 只在仓库根目录 `.env` 中维护，Compose 将其注入进程环境；
  应用据此读取 `configs/.env.{APP_ENV}` 和根目录 `.secrets`。非敏感运行参数必须在环境配置中显式声明，
  `.secrets` 只允许凭据字段；除 `APP_ENV` 外不使用 shell 环境变量兜底。
- 密钥安全：不要提交真实密钥、真实数据库口令、真实 token 或生产配置；日志上下文不得泄露 secret、
  token、密码、数据库连接串明文。
- 数据库：写操作通过 DAO 或已有 builder/helper 完成，不要在 controller 里直接操作 session。
- 数据变更：涉及 schema、表结构、持久化格式或查询行为时，同步检查 `ddl/`、相关 docs、测试 fixture，
  并说明迁移、回滚、锁表和部署顺序风险。
- 缓存：保持 Redis 缓存 key 格式、TTL 策略和业务域封装；细则见 `internal/cache/AGENTS.md`。
- Celery：保持 task name、队列和 Beat 调度兼容；细则见 `internal/tasks/AGENTS.md`。
- 向量检索：保持 `pkg/vectors/` contract；细则见 `pkg/vectors/AGENTS.md`、
  `pkg/vectors/backends/milvus/AGENTS.md` 和对应 README。
- 容器部署：根目录 `Dockerfile`、`compose.yaml`、`.env.compose.example` 与 `deploy/`、日志文档共同构成部署
  contract；保持 API 非 root、应用配置/密钥只读挂载、logrotate 单副本和最小权限边界。

## 子目录细则

进入以下区域前读取对应文件，按最近的 `AGENTS.md` 优先：

- 应用装配 / 配置：`internal/AGENTS.md`、`configs/AGENTS.md`
- Controller / response envelope：`internal/controllers/AGENTS.md`
- Service 和依赖注入：`internal/services/AGENTS.md`
- Agent Service / 会话 / 确认 / 审计：`internal/services/agents/AGENTS.md`
- Schema：`internal/schemas/AGENTS.md`
- Middleware / auth / request context：`internal/middlewares/AGENTS.md`
- Infra provider / lifecycle：`internal/infra/AGENTS.md`
- Model / DAO / Cache：`internal/models/AGENTS.md`、`internal/dao/AGENTS.md`、`internal/cache/AGENTS.md`
- RAG 业务 repository：`internal/rag/AGENTS.md`
- 业务 Agent：`internal/agents/AGENTS.md` 及领域目录内更具体的 `AGENTS.md`
- Celery task / schedule：`internal/tasks/AGENTS.md`
- 进程入口：`entrypoints/AGENTS.md`
- Database base / query builder：`pkg/database/AGENTS.md`
- Logger：`pkg/logger/AGENTS.md`
- OpenTelemetry tracing：`pkg/tracing/AGENTS.md`
- LLM client abstraction：`pkg/llm/AGENTS.md`
- Vector abstraction：`pkg/vectors/AGENTS.md`
- 部署 / 脚本 / SQL：`deploy/AGENTS.md`、`scripts/AGENTS.md`、`ddl/AGENTS.md`、`dml/AGENTS.md`
- 文档 / 计划：`docs/AGENTS.md`、`docs/plans/AGENTS.md`
- Tests：`tests/AGENTS.md`

## Python 与验证

- Python 版本为 `3.12+`。
- 遵循 `pyproject.toml`：Ruff line length `120`，规则启用 `E`、`W`、`F`、`I`、`B`、`UP`；
  格式化使用双引号和空格缩进。
- 常用安装、启动、测试、lint、type-check 命令见 `README.md`。执行命令前以当前 `pyproject.toml` 和锁文件为准。
- 运行最小但有意义的验证覆盖改动行为；不要声称未运行的测试或兼容性。
- 集成测试默认不假设 Redis、Celery Worker、Milvus 或数据库已运行，应标记 `integration`。
