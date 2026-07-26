# agent-backend

AGENT-BACKEND 是面向 Agent、RAG 与 AI 平台场景的 FastAPI demo 合集与后端脚手架。它的目标是沉淀可复用的后端工程范式和示例能力，便于快速验证 Agent 运行时、RAG、认证、异步数据库访问、Redis、Celery、请求日志、第三方登录和向量检索等基础设施；它不是一套需要长期承载真实业务流量的单一业务服务。

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
- 向量后端：`pymilvus`

## 当前能力

- 进程入口：`entrypoints/main.py` 导出 FastAPI `app`，`internal.infra.celery` 导出 Celery `celery_app`，`internal/app.py` 负责路由、中间件和 lifespan。
- 配置加载：从 `configs/.secrets` 读取 `APP_ENV`，加载 `configs/.env.{APP_ENV}` 后再由 `.secrets` 覆盖。
- 数据库：异步 SQLAlchemy 连接池，支持主库和可选只读副本；配置层支持 PostgreSQL、MySQL、Oracle DSN。
- Redis：连接池、业务缓存封装、认证 token metadata、Agent action confirmation / idempotency 缓存。
- 认证：Token 认证、内部签名认证、公共路由白名单、微信登录接入点。
- 中间件：请求日志、CORS、Endpoint Guard、认证、GZip。
- Agent：通用结构化 ReAct / Tool Calling 执行循环，统一 Router Agent，订单售后 Agent，支付支持 Agent。
- Celery：Worker、Beat、任务路由和示例任务骨架。
- 向量检索：通用 repository / backend 抽象，包含 Milvus backend。
- 基础包：日志、数据库、OSS / S3、第三方登录、加解密、通用 toolkit。

## 环境要求

- Python `3.12+`
- `uv`
- Redis
- PostgreSQL（当前依赖默认包含 `asyncpg`，仓库配置示例也默认使用 PostgreSQL）

配置层支持 `mysql`、`postgresql`、`oracle` 三种 `DB_TYPE`，但当前 `pyproject.toml` 默认只包含 PostgreSQL 异步驱动 `asyncpg`。如果切换到 MySQL 或 Oracle，需要先补充对应运行时驱动，例如 `aiomysql` 或 `oracledb`，再同步锁文件和部署环境。

使用向量集成测试或真实向量检索时，还需要准备对应的 Milvus 后端依赖。

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
EMBEDDING_API_KEY=
```

`configs/.env.local`

```env
DEBUG=true
LOG_FORMAT=text
LOG_BASE_DIR=logs
LOG_WRITE_TO_FILE=true
LOG_WRITE_TO_CONSOLE=true
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

EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_BASE_URL=http://bge-embedding:8000/v1
EMBEDDING_MODEL=bge-m3
EMBEDDING_DIMENSION=1024
EMBEDDING_TIMEOUT_SECONDS=60

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
- Logger 只负责控制台输出和向 `LOG_BASE_DIR/app.log` 追加日志，不在应用内执行轮转、保留或压缩。
- 启用 `LOG_WRITE_TO_FILE=true` 的长期运行环境必须同时配置外部日志轮转；部署方案见 `docs/logging/log_rotation.md`。
- `LLM_*` 用于 Chat / Agent 推理模型；`EMBEDDING_*` 用于向量模型，两者不要共用配置。
- `EMBEDDING_PROVIDER` 当前支持 `openai_compatible`，可接 OpenAI-compatible `/embeddings` 协议的 BGE、vLLM、Xinference、TEI 或自建向量服务。
- `EMBEDDING_DIMENSION` 必须与向量模型输出维度和向量 collection schema 保持一致。
- `ENDPOINT_GUARD_RULES` 是 JSON 数组字符串，可按 method、path、match type 禁用或拒绝指定 endpoint。

### 3. 启动 API

开发模式：

```bash
uv run uvicorn entrypoints.main:app --reload --host 0.0.0.0 --port 8000
```

非热重载模式：

```bash
uv run uvicorn entrypoints.main:app --host 0.0.0.0 --port 8000
```

当 `DEBUG=true` 时，FastAPI 文档可访问：

- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/redoc`

### 4. 启动 Celery

Worker：

```bash
uv run celery -A internal.infra.celery:celery_app worker -l info -Q default,celery_queue,cron_queue,celery_maintenance_queue
```

使用 PostgreSQL 幂等任务前，先在全新环境执行
`ddl/postgresql/1.2.0_celery_task_state_machine.sql`。该脚本不迁移旧表；已有环境必须人工确认并删除旧的
`celery_task_record` 后再执行。API 先登记 `SUBMITTING` 记录，再调用 `CeleryClient.submit()`；Worker 可从
`SUBMITTING` 或 `QUEUED` claim，从而避免快速消费竞态。Beat 会分别收敛发布确认超时、排队启动超时和
执行 deadline 超时；运行中任务超过 hard deadline 后先进入 `ORPHANED`，等待 fence 过期和业务核对后再进入
`FAILED/CANCELLED`，不会自动重新提交任务。运行中任务可通过 `cancel_allowed=false` 进入不可取消阶段。取消接口先持久化
`CANCELLED/CANCELLING`，再 best-effort
调用非强制 Celery revoke，运行中任务通过数据库检查点协作退出。

状态机相关配置：

- `CELERY_QUEUE_START_TIMEOUT_SECONDS`：消息确认入队后允许等待 Worker claim 的时间，默认 300 秒。
- `CELERY_EXECUTION_DEADLINE_GRACE_SECONDS`：task time limit 之外的状态收敛宽限，默认 30 秒。
- `CELERY_ORPHAN_FENCE_SECONDS`：`ORPHANED` 状态外部副作用隔离窗口，默认 300 秒。
- `CELERY_PUBLISH_CONFIRM_TIMEOUT_SECONDS`：`SUBMITTING` 发布确认超时。
- `CELERY_PUBLISH_RECONCILER_INTERVAL_SECONDS`：发布确认 Reconciler 周期。
- `CELERY_RECONCILER_INTERVAL_SECONDS` / `CELERY_RECONCILER_BATCH_SIZE`：deadline Reconciler 周期与批量。
- `CELERY_RECONCILER_QUEUE`：Reconciler 专用 queue。

Beat：

```bash
uv run celery -A internal.infra.celery:celery_app beat -l info
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
| `POST` | `/v1/celery-tasks/idempotent-sum/create` | 登记幂等 Celery 加法 demo |
| `GET` | `/v1/celery-tasks/{record_id}` | 查询当前用户的逻辑任务状态 |
| `POST` | `/v1/celery-tasks/{record_id}/cancel` | 幂等请求取消逻辑任务 |
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
- 精确白名单包含 `/v1/auth/login`、`/v1/auth/register`、`/v1/auth/wechat/login`、`/docs`、`/openapi.json`。

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

AgentScope 2.0 的推荐接入路线是嵌入官方 Agent Service，而不是自建 KB Copilot chat API。
该路线尚未落地到代码，详细方案见 `docs/agent/agentscope_service_integration.md` 和
`docs/plans/AGENTSCOPE_KB_COPILOT_EXECUTION_PLAN.md`。

相关文档：

- `docs/agent/agentscope_service_integration.md`
- `pkg/agents/README.md`
- `internal/agents/order/README.md`
- `internal/agents/payment/README.md`

## 项目结构

- `entrypoints/`：进程入口，包含 FastAPI、Celery Worker / Beat 启动入口。
- `internal/`：业务应用代码，包含路由、中间件、配置、服务、DAO、缓存、模型、Schema、任务和业务 Agent。
- `pkg/`：可复用基础包，包含通用 Agent、数据库、日志、OSS、第三方登录、加解密、toolkit 和向量检索抽象。
- `examples/`：可运行示例代码，不作为生产运行时基础包。
- `configs/`：环境配置模板与密钥模板。
- `docs/`：补充文档。
- `tests/`：pytest 测试。
- `ddl/`、`dml/`：数据库 SQL。

更细的目录职责和修改约束见各目录下的 `AGENTS.md`；模块级使用说明优先看对应 `README.md`。

## 向量检索

通用向量抽象位于 `pkg/vectors/`，当前 backend：

- `pkg.vectors.backends.milvus`

接入 RAG、知识库检索或混合检索前，先读：

- `pkg/vectors/AGENTS.md`
- `pkg/vectors/backends/milvus/README.md`

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

运行容器时需要提供配置文件和可访问的 PostgreSQL / Redis / LLM provider / Embedding provider 等外部依赖。示例：

```bash
docker run --rm -p 8000:8000 \
  -v "$PWD/configs/.secrets:/app/configs/.secrets:ro" \
  -v "$PWD/configs/.env.local:/app/configs/.env.local:ro" \
  agent-backend
```

如果容器内无法访问宿主机服务，需要把 `DB_HOST`、`REDIS_HOST` 等配置改成容器网络可解析的地址。

### 生产 Compose 与 logrotate sidecar

`compose.prod.yaml` 面向单台 Linux 主机，运行一个可配置多 Worker 的 API service 和一个单副本
logrotate sidecar。两者共享宿主机日志目录，活动日志固定为 `app.log`；sidecar 每 5 分钟检查一次，负责
`copytruncate`、压缩和保留 30 个归档。该方案不提供审计级零丢失保证，也不用于 Kubernetes。

部署前需要 Docker Engine、Docker Compose v2、可访问的 PostgreSQL/Redis 等外部依赖，以及包含
`APP_ENV=prod` 和真实密钥的 `configs/.secrets`。Compose 部署参数使用根目录 `.env`，应用配置和密钥仍通过
只读文件挂载加载：

```bash
cp .env.compose.example .env
```

至少按目标主机调整 `.env` 中的 `AGENT_BACKEND_LOG_DIR`、`API_WORKERS`、镜像、端口和资源限制。
`.env` 不应存放应用密钥，也不会提交到 Git。

先构建镜像并查询 API 镜像内 `app` 用户的数字 UID/GID：

```bash
docker compose -f compose.prod.yaml build api logrotate
API_UID=$(docker run --rm --entrypoint id agent-backend:prod -u)
API_GID=$(docker run --rm --entrypoint id agent-backend:prod -g)
sudo chown "$API_UID:$API_GID" configs/.secrets
sudo chmod 0400 configs/.secrets
sudo install -d -m 0750 -o "$API_UID" -g "$API_GID" /var/log/agent-backend
```

如果修改了 `.env` 中的 `AGENT_BACKEND_IMAGE` 或 `AGENT_BACKEND_LOG_DIR`，上面的镜像名和目录也必须使用相同值。
密钥文件必须允许镜像内的非 root `app` 用户读取；上述数字 UID/GID 归属只影响宿主机文件元数据，文件仍以只读方式
挂载到容器。每次更换为不同 UID/GID 的 API 镜像后都应重新执行权限设置。
确认配置展开结果只包含密钥文件路径、不包含密钥内容，然后依次启动 sidecar 和 API：

```bash
docker compose -f compose.prod.yaml config
docker compose -f compose.prod.yaml up -d logrotate
docker compose -f compose.prod.yaml up -d api
docker compose -f compose.prod.yaml ps
```

常用检查和停止命令：

```bash
docker compose -f compose.prod.yaml logs --tail=200 -f logrotate
docker compose -f compose.prod.yaml logs --tail=200 -f api
docker compose -f compose.prod.yaml exec logrotate \
  logrotate --debug --state /var/lib/logrotate/status /etc/logrotate.d/agent-backend
docker compose -f compose.prod.yaml down
```

`down` 默认保留 `logrotate-state` named volume 和宿主机日志。不要使用 `down --volumes`，除非已经明确批准删除
轮转状态。目标 Linux 环境的完整多 Worker 验收使用显式隔离目录运行：

```bash
./scripts/verify_logrotate_sidecar.sh /tmp/agent-backend-logrotate-acceptance
```

验收脚本会创建自己的临时子目录和 Compose project，结束时删除测试容器、测试 state volume 和测试镜像，但保留
隔离目录中的日志归档供检查。它拒绝在 macOS 或生产日志目录上运行。详细权限、轮转和故障处理见
`docs/logging/log_rotation.md`。隔离目录还必须位于仓库之外，避免测试 secrets 或日志进入 Docker build context。

## 相关文档

- `docs/README.md`
- `pkg/agents/README.md`
- `pkg/vectors/backends/milvus/README.md`

## License

MIT
