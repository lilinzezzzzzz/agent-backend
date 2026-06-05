# agent-backend

面向 Agent、RAG 与 AI 平台场景的 FastAPI 后端基础设施骨架。它不是单一业务服务，而是一个可继续扩展的后端模板，已经包含 Web API、认证、异步数据库访问、Redis、Celery、请求日志、第三方登录、Agent 执行循环和向量检索抽象。

README 只记录当前仓库可直接核对的能力；更细的设计约束和模块说明见 `docs/`、`pkg/*/README.md` 以及各目录下的 `AGENTS.md`。

## 技术栈

- Python `3.12+`
- `uv`
- FastAPI + Starlette
- Pydantic v2 + `pydantic-settings`
- SQLAlchemy 2.x Async ORM
- Redis
- Celery + Beat
- anyio
- Loguru
- OpenAI-compatible LLM client
- 向量后端：`zvec`、`pymilvus`

## 当前能力

- 进程入口：`entrypoints/api.py` 导出 FastAPI `app`，`entrypoints/celery.py` 导出 Celery `celery_app`，`internal/app.py` 负责路由、中间件和 lifespan。
- 配置加载：从 `configs/.secrets` 读取 `APP_ENV`，加载 `configs/.env.{APP_ENV}` 后再由 `.secrets` 覆盖。
- 数据库：异步 SQLAlchemy 连接池，支持主库和可选只读副本；配置层支持 PostgreSQL、MySQL、Oracle DSN。
- Redis：连接池、业务缓存封装、认证 token metadata、Agent action confirmation / idempotency 缓存。
- 认证：Token 认证、内部签名认证、公共路由白名单、微信登录接入点。
- 中间件：请求日志、CORS、Endpoint Guard、认证、GZip。
- Agent：通用结构化 ReAct / Tool Calling 执行循环，统一 Router Agent，订单售后 Agent，支付支持 Agent。
- Celery：Worker、Beat、任务路由和示例任务骨架。
- 向量检索：通用 repository / backend 抽象，包含 Milvus 和 zvec backend。
- 基础包：日志、数据库、OSS / S3、第三方登录、加解密、通用 toolkit。

## 环境要求

- Python `3.12+`
- `uv`
- Redis
- PostgreSQL（当前依赖默认包含 `asyncpg`，仓库配置示例也默认使用 PostgreSQL）

配置层支持 `mysql`、`postgresql`、`oracle` 三种 `DB_TYPE`，但当前 `pyproject.toml` 默认只包含 PostgreSQL 异步驱动 `asyncpg`。如果切换到 MySQL 或 Oracle，需要先补充对应运行时驱动，例如 `aiomysql` 或 `oracledb`，再同步锁文件和部署环境。

使用向量集成测试或真实向量检索时，还需要准备对应的 Milvus、zvec 或其他后端依赖。

## 快速开始

### 1. 安装依赖

开发环境：

```bash
uv sync --group dev
```

只安装运行时依赖：

```bash
uv sync --frozen
```

### 2. 准备配置

复制密钥模板：

```bash
cp configs/.secrets.example configs/.secrets
```

启动时配置加载顺序如下：

1. 从 `configs/.secrets` 读取 `APP_ENV`。
2. 加载 `configs/.env.{APP_ENV}`。
3. 再加载 `configs/.secrets`，同名配置覆盖 `.env`。
4. 外部 shell 环境变量只作为缺省兜底，不覆盖配置文件中已有 key。

缺少 `configs/.secrets`、缺少 `APP_ENV`，或缺少对应的 `configs/.env.{APP_ENV}`，应用会在启动阶段失败。

最小本地配置示例：

`configs/.secrets`

```env
APP_ENV=local
AES_SECRET=your_aes_secret_key
JWT_SECRET=your_jwt_secret_key
DB_PASSWORD=your_db_password
REDIS_PASSWORD=
LLM_MIMO_API_KEY=your_mimo_api_key
LLM_DEEPSEEK_API_KEY=your_deepseek_api_key
```

`configs/.env.local`

```env
DEBUG=true
LOG_FORMAT=text
CONFIG_ECHO_REVEAL_SECRETS=false
JWT_ALGORITHM=HS256

DB_TYPE=postgresql
DB_HOST=127.0.0.1
DB_PORT=5432
DB_USERNAME=postgres
DB_DATABASE=agent_backend
DB_ECHO=true

REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0

ACCESS_TOKEN_EXPIRE_MINUTES=30
AGENT_ACTION_CONFIRMATION_SECONDS=300
AGENT_ACTION_IDEMPOTENCY_SECONDS=86400

LLM_DEFAULT_PROVIDER=mimo
LLM_MIMO_BASE_URL=https://api.xiaomimimo.com/v1
LLM_MIMO_MODEL=mimo-v2.5-pro
LLM_DEEPSEEK_BASE_URL=https://api.deepseek.com
LLM_DEEPSEEK_MODEL=deepseek-v4-flash

ENDPOINT_GUARD_ENABLED=true
ENDPOINT_GUARD_SOURCE=settings
ENDPOINT_GUARD_RULES=[]
ENDPOINT_GUARD_REDIS_KEY=endpoint_guard:rules
ENDPOINT_GUARD_CACHE_TTL_SECONDS=10
ENDPOINT_GUARD_FAIL_OPEN=true
```

配置说明：

- 仓库已提供 `configs/.env.local`、`configs/.env.dev`、`configs/.env.test`、`configs/.env.prod`。
- `configs/.secrets` 被 `.gitignore` 忽略，不要提交真实密钥、数据库口令、token 或生产配置。
- `DB_PASSWORD`、`DB_READ_PASSWORD`、`REDIS_PASSWORD` 支持 `ENC(...)`，运行时使用 `AES_SECRET` 解密。
- 设置 `DB_READ_HOST` 后会初始化只读副本；未设置时读会话自动 fallback 到主库。
- `CONFIG_ECHO_REVEAL_SECRETS=true` 会在启动配置回显中显示敏感值，只应临时用于本地诊断。
- `ENDPOINT_GUARD_RULES` 是 JSON 数组字符串，可按 method、path、match type 禁用或拒绝指定 endpoint。

### 3. 启动 API

开发模式：

```bash
uv run uvicorn entrypoints.api:app --reload --host 0.0.0.0 --port 8000
```

非热重载模式：

```bash
uv run uvicorn entrypoints.api:app --host 0.0.0.0 --port 8000
```

当 `DEBUG=true` 时，FastAPI 文档可访问：

- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/redoc`

### 4. 启动 Celery

Worker：

```bash
uv run celery -A entrypoints.celery:celery_app worker -l info -Q default,celery_queue,cron_queue
```

Beat：

```bash
uv run celery -A entrypoints.celery:celery_app beat -l info
```

也可以使用脚本启动 Worker：

```bash
./entrypoints/run_celery_worker.sh
```

脚本支持：

- `CELERY_LOG_LEVEL`
- `CELERY_CONCURRENCY`
- `CELERY_QUEUES`

## API 与认证

路由前缀：

| 前缀 | 说明 | 默认认证策略 |
| --- | --- | --- |
| `/v1` | 业务 API | Token |
| `/v1/public` | 公共接口 | 无 |
| `/v1/internal` | 内部接口 | 签名认证 |

当前已挂载的主要接口：

| Method | Path | 说明 |
| --- | --- | --- |
| `POST` | `/v1/auth/login` | 用户登录 |
| `POST` | `/v1/auth/register` | 用户注册 |
| `POST` | `/v1/auth/logout` | 用户登出 |
| `GET` | `/v1/auth/me` | 当前用户信息 |
| `POST` | `/v1/auth/wechat/login` | 微信登录 |
| `GET` | `/v1/user/hello-world` | 用户示例接口 |
| `POST` | `/v1/agent/chat` | 统一 Agent 聊天入口 |
| `POST` | `/v1/agent/order/support` | 订单售后 Agent |
| `POST` | `/v1/agent/payment/support` | 支付支持 Agent |
| `POST` | `/v1/public/test/test_validation_error` | 请求校验异常测试 |
| `GET` | `/v1/public/test/test_raise_exception` | 通用异常测试 |
| `GET` | `/v1/public/test/test_raise_app_exception` | 应用异常测试 |
| `GET` | `/v1/public/test/test_contextvars_on_asyncio_task` | contextvars 异步任务测试 |
| `GET` | `/v1/public/test/test/sse-stream` | SSE 流式响应测试 |
| `GET` | `/v1/public/test/chat/sse-stream/timeout` | SSE chunk timeout 测试 |

认证中间件规则：

- `/v1/public/**` 直接放行。
- `/v1/internal/**` 使用 `X-Signature`、`X-Timestamp`、`X-Nonce` 做签名认证。
- 其他 HTTP 接口默认要求 `Authorization` token，兼容裸 token 和 `Bearer <token>`。
- 精确白名单包含 `/auth/login`、`/auth/register`、`/auth/wechat/login`、`/docs`、`/openapi.json`。

统一成功响应结构：

```json
{
  "code": 20000,
  "message": "",
  "data": {}
}
```

## Agent 模块

通用 Agent 基础设施位于 `pkg/agents/`，当前核心是结构化 ReAct / Tool Calling：

- `ReActAgent`：有限循环执行器。
- `AgentActionMaker`：根据上下文生成下一步动作。
- `StructuredTool`：声明工具名、描述、参数 schema 和 handler。
- `AgentRunState` / `AgentStepRecord`：记录动作、action_result、错误和耗时。

业务 Agent 位于 `internal/agents/`：

- `internal/agents/router.py`：统一业务域路由。
- `internal/agents/order/`：订单、物流、售后、退款、发票咨询和开票两阶段确认。
- `internal/agents/payment/`：支付方式、付款失败、扣款异常、账单、分期和金额计算咨询。

相关文档：

- `pkg/agents/README.md`
- `internal/agents/order/README.md`
- `internal/agents/payment/README.md`

## 项目结构

```text
.
├── entrypoints/                # 进程启动入口
│   ├── api.py                  # FastAPI 应用入口，导出 app
│   ├── celery.py               # Celery CLI 入口，导出 celery_app
│   └── run_celery_worker.sh    # Celery Worker 启动脚本
├── main.py                     # 兼容旧部署的 FastAPI 入口，推荐使用 entrypoints/api.py
├── internal/                   # 业务应用代码
│   ├── app.py                  # 应用组装、路由、中间件、lifespan
│   ├── config.py               # 配置模型和加载逻辑
│   ├── controllers/            # API 路由
│   │   ├── api/                # /v1
│   │   ├── public/             # /v1/public
│   │   └── internal/           # /v1/internal
│   ├── middlewares/            # 请求日志、认证、Endpoint Guard 等中间件
│   ├── infra/                  # DB、Redis、LLM client、Celery、调度器等基础设施
│   ├── services/               # 业务用例与跨 DAO / 缓存 / 外部服务编排
│   ├── agents/                 # 业务 Agent prompt、builder、tools
│   ├── dao/                    # ORM 数据访问层
│   ├── cache/                  # Redis 业务缓存封装
│   ├── models/                 # SQLAlchemy ORM 模型
│   ├── schemas/                # 请求和响应 Schema
│   ├── tasks/                  # Celery 任务与 Beat 配置
│   └── utils/                  # 应用内工具
├── pkg/                        # 可复用基础包
│   ├── agents/                 # 通用 Agent 执行循环
│   ├── database/               # Async ORM 基础设施
│   ├── logger/                 # Loguru 日志封装和 span
│   ├── crypter/                # AES 加解密
│   ├── oss/                    # OSS / S3 抽象
│   ├── third_party_auth/       # 第三方登录策略
│   ├── toolkit/                # 通用工具集
│   └── vectors/                # 向量检索抽象与 backend
├── examples/                   # 可运行示例代码，不作为生产运行时基础包
│   └── llm/                    # LLM function call、MCP SSE 示例
├── configs/                    # 环境配置模板与密钥模板
├── docs/                       # 补充文档
├── tests/                      # pytest 测试
├── ddl/                        # DDL SQL
└── dml/                        # DML SQL
```

## 向量检索

通用向量抽象位于 `pkg/vectors/`，当前 backend：

- `pkg.vectors.backends.milvus`
- `pkg.vectors.backends.zvec`

接入 RAG、知识库检索或混合检索前，先读：

- `pkg/vectors/AGENTS.md`
- `pkg/vectors/backends/milvus/README.md`
- `pkg/vectors/backends/zvec/README.md`

## 测试与质量检查

运行全部测试：

```bash
uv run pytest
```

运行单个测试文件：

```bash
uv run pytest tests/api/test_auth.py -v
```

运行 Agent 相关测试：

```bash
uv run pytest tests/agents tests/api/test_agent.py -v
```

运行 Endpoint Guard 测试：

```bash
uv run pytest tests/middlewares/test_endpoint_guard.py -v
```

类型检查：

```bash
uv run mypy .
```

格式化检查可使用当前 dev 组中的 Black：

```bash
uv run black --check .
```

项目约定也包含 Ruff 检查命令，但当前 `pyproject.toml` 的 dev 依赖组没有声明 `ruff`；如果团队环境已安装 Ruff，可运行：

```bash
uv run ruff check .
uv run ruff format --check .
```

集成测试默认不假设 Redis、Celery Worker、Milvus 或数据库已经运行；依赖外部服务的测试应使用 `integration` marker。

## Docker

构建镜像：

```bash
docker build -t agent-backend .
```

运行容器时需要提供配置文件和可访问的 PostgreSQL / Redis / LLM provider 等外部依赖。示例：

```bash
docker run --rm -p 8000:8000 \
  -v "$PWD/configs/.secrets:/app/configs/.secrets:ro" \
  -v "$PWD/configs/.env.local:/app/configs/.env.local:ro" \
  agent-backend
```

如果容器内无法访问宿主机服务，需要把 `DB_HOST`、`REDIS_HOST` 等配置改成容器网络可解析的地址。

## 相关文档

- `docs/auth_module_guide.md`
- `docs/third_party_login_guide.md`
- `docs/uv_use_guide.md`
- `docs/dataclass_use_guide.md`
- `docs/md_use_guide.md`
- `pkg/agents/README.md`
- `pkg/vectors/backends/milvus/README.md`
- `pkg/vectors/backends/zvec/README.md`

## License

MIT
