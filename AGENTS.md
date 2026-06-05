# AGENTS.md

本文件适用于整个仓库，定位为**项目特定约束**

## 项目概览

这是一个面向 Agent、RAG 与 AI 平台场景的后端基础设施项目，基于 FastAPI，定位是可扩展的 Agent 后端骨架，而不是单一业务服务。当前能力包括 Web API、认证、异步数据库访问、Redis 缓存、Celery 任务、请求日志、第三方登录，以及可复用的向量检索抽象。

主要技术栈：

- Python `3.12+`
- `uv`
- FastAPI + Starlette
- Pydantic v2 + `pydantic-settings`
- SQLAlchemy 2.x Async ORM
- Redis
- Celery + Beat
- anyio
- Loguru
- 向量后端：`zvec`、`pymilvus`

## 目录结构

- `entrypoints/`：进程启动入口，放置 API、Celery Worker / Beat 等 composition root。
- `entrypoints/api.py`：FastAPI 推荐应用入口，导出 `app`。
- `entrypoints/celery.py`：Celery CLI 推荐入口，导出 `celery_app`。
- `entrypoints/run_celery_worker.sh`：Celery Worker 启动脚本。
- `main.py`：兼容旧部署的 FastAPI 应用入口，新配置优先使用 `entrypoints.api:app`。
- `internal/`：业务应用代码。
- `internal/app.py`：应用组装、路由注册、中间件注册、lifespan 初始化。
- `internal/config.py`：配置模型和配置加载逻辑。
- `internal/controllers/`：API 路由。
- `internal/controllers/api/`：`/v1` 业务接口。
- `internal/controllers/public/`：`/v1/public` 公共接口。
- `internal/controllers/internal/`：`/v1/internal` 内部接口。
- `internal/middlewares/`：认证、请求记录等 ASGI 中间件。
- `internal/infra/`：数据库、Redis 等基础设施连接。
- `internal/services/`：业务逻辑。
- `internal/services/dto/`：Service 对外返回给 Controller 的业务 DTO。
- `internal/dao/`：ORM 数据访问层。
- `internal/cache/`：业务缓存层（按业务域组织的 Redis 访问）。
- `internal/models/`：SQLAlchemy ORM 模型。
- `internal/schemas/`：请求和响应 Schema。
- `internal/tasks/`：Celery 任务与调度配置。
- `internal/utils/`：应用内工具。
- `pkg/`：可复用基础包，不应绑定具体业务。
- `pkg/database/`：Async ORM 基础设施、DAO、查询构建器。
- `pkg/toolkit/`：通用工具。
- `pkg/logger/`：Loguru 日志封装和 span 工具。
- `pkg/vectors/`：向量检索抽象、repository、backend。
- `configs/`：环境配置与密钥文件。
- `tests/`：pytest 测试。
- `docs/`：补充文档。
- `ddl/`、`dml/`：数据库 SQL。

## 分层命名规则

跨 Controller / Service / DTO / DAO / Model / Schema 的通用命名在根目录统一定义，子目录
`AGENTS.md` 不重复维护同一套规则；只补充该层特有约束。

- `{Resource}`：ORM / domain entity，映射数据库表和持久化字段，例如 `Account`、
  `Order`、`KnowledgeBase`。
- `{Resource}Dao`：持久化访问对象，封装对应 ORM model 的查询和写入，例如
  `AccountDao`、`OrderDao`。
- `{Resource}Service`：业务用例入口，承载领域规则、跨 DAO / 缓存 / 外部服务编排，
  例如 `AccountService`、`OrderService`。
- `{Action}DTO` 或 `{Resource}{Action}DTO`：Service 返回给 Controller 的业务数据；
  Service 对外返回业务 DTO 时优先使用 dataclass 定义，命名使用 `*DTO` 后缀，
  统一放在 `internal/services/dto/<domain>.py`，例如 `LoginDTO`、`AccountLoginDTO`、
  `OrderSummaryDTO`。
- `{Action}ReqSchema` 或 `{Resource}{Action}ReqSchema`：API request schema，用于
  请求体、查询参数或路径参数的传输层校验，例如 `LoginReqSchema`、
  `AccountLoginReqSchema`。
- `{Action}RespSchema` 或 `{Resource}{Action}RespSchema`：API response schema，用于
  `response_model`、响应数据结构和 OpenAPI contract，例如 `LoginRespSchema`、
  `AccountLoginRespSchema`。
- `{Resource}DetailSchema` / `{Resource}ItemSchema`：API detail / item schema，用于
  响应中的资源详情或列表项，例如 `AccountDetailSchema`、`OrderItemSchema`。

## 常用命令

安装开发依赖：

```bash
uv sync --group dev
```

启动 API：

```bash
uv run uvicorn entrypoints.api:app --reload --host 0.0.0.0 --port 8000
```

启动 Celery Worker：

```bash
uv run celery -A entrypoints.celery:celery_app worker -l info -Q default,celery_queue,cron_queue
```

启动 Celery Beat：

```bash
uv run celery -A entrypoints.celery:celery_app beat -l info
```

运行测试：

```bash
uv run pytest
```

运行单个测试文件：

```bash
uv run pytest tests/api/test_auth.py -v
```

代码检查和格式化：

```bash
uv run ruff check .
uv run ruff format .
uv run mypy .
```

## 配置约定

应用配置由 `internal/config.py` 管理。

- 启动时先读取 `configs/.secrets` 中的 `APP_ENV`。
- 再加载 `configs/.env.{APP_ENV}`。
- 最后由 `configs/.secrets` 覆盖同名配置。
- 缺失 `configs/.secrets`、缺失 `APP_ENV`、或缺失对应 `.env` 文件时，启动应直接失败。
- `DB_PASSWORD`、`DB_READ_PASSWORD`、`REDIS_PASSWORD` 支持 `ENC(...)`，运行时使用 `AES_SECRET` 解密。
- 不要提交真实密钥、真实数据库口令、真实 token 或生产配置。

修改配置行为时，同步检查：

- `internal/config.py`
- `configs/.env.*`
- README 或 `docs/` 中相关说明
- 依赖配置的测试 fixture

## API 约定

路由前缀：

- `/v1`：业务 API，默认需要 token。
- `/v1/public`：公共接口，默认无认证。
- `/v1/internal`：内部接口，默认签名认证。

认证相关规则主要在 `internal/middlewares/auth.py`、`internal/services/auth.py`、`internal/controllers/api/auth.py`。

Controller / Service / DAO / Schema 分层细则见各子目录 `AGENTS.md`：

- `internal/controllers/AGENTS.md`：Controller 层规范与响应信封。
- `internal/services/AGENTS.md`：业务用例、依赖注入单例。
- `internal/schemas/AGENTS.md`：请求 / 响应 Schema 规范。
- `internal/middlewares/AGENTS.md`：认证、签名、上下文中间件。

## 数据库约定

数据库基础设施位于 `pkg/database/` 和 `internal/infra/database/`，细则见：

- `pkg/database/AGENTS.md`：ORM 基类、builder、session provider。
- `internal/infra/AGENTS.md`：连接生命周期和 provider。
- `internal/models/AGENTS.md`：ORM 模型和表结构约束。
- `internal/dao/AGENTS.md`：ORM DAO 实现、查询 builder。
- `internal/cache/AGENTS.md`：业务缓存封装、key 约定、TTL 策略。

跨层硬约束：

- 涉及 schema 或数据变更时，要同步 `ddl/`、文档和测试，并说明迁移、回滚、锁表、部署顺序风险。
- 写操作通过 DAO 或已有 builder/helper 完成，不要在 controller 里直接操作 session。

## Redis 和 Celery

- 业务缓存访问见 `internal/cache/AGENTS.md`（依照业务域划分，如 `internal/cache/auth.py`）。
- Celery 任务、队列、定时调度细则见 `internal/tasks/AGENTS.md`。
- Redis / DB 连接生命周期见 `internal/infra/AGENTS.md`。

## 向量检索约定

向量抽象位于 `pkg/vectors/`，约定和兼容性要求见 `pkg/vectors/AGENTS.md` 和对应 backend 目录的 `README.md`。

## Python 代码风格

通用 Python 规范见全局 `~/.qoder/AGENTS.md` Python 章节。项目特有约束：

- 遵循 `pyproject.toml`：Ruff line length `120`，规则启用 `E`、`W`、`F`、`I`、`B`、`UP`；格式化使用双引号和空格缩进。
- Python 版本锁定 `3.12+`，可使用 3.12 新语法。
- 日志上下文不得泄露 secret、token、密码、数据库连接串明文。

## 测试约定

- 运行测试统一使用 `uv run pytest`；子目录详细约定见 `tests/AGENTS.md`。
- 集成测试默认不假设 Redis、Celery Worker、Milvus 或数据库已运行，应标记 `integration`。

## 项目特定的修改边界

通用修改流程（Discovery / Execution Protocol / 测试验证）见全局 `~/.qoder/AGENTS.md`。本仓库的额外硬约束：

- 修改前确认是否涉及项目特定边界：API contract、认证/签名中间件、缓存策略、数据库 schema 和 `ddl/`、Celery 任务名和队列、第三方登录、向量契约。
- 项目内默认复用已有 service / DAO / schema / toolkit / middleware，不新增并行抽象。
- 需保持兼容的项目级契约：路由前缀、`internal.core.errors` 错误码、Celery task name、Redis 缓存 key 格式、`pkg/vectors/` contract、响应信封 `BaseResponse[T]`。
