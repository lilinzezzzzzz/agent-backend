# agent-backend

AGENT-BACKEND 是面向 Agent、RAG 与 AI 平台场景的 FastAPI demo 合集与后端脚手架。它的目标是沉淀可复用的后端工程范式和示例能力，便于快速验证 Agent 运行时、RAG、认证、异步数据库访问、Redis、Celery、请求日志、第三方登录和向量检索等基础设施；它不是一套需要长期承载真实业务流量的单一业务服务。

本仓库仅提供后端代码与 API，不包含 demo 前端或静态页面托管；开启 debug 时可使用 FastAPI 自带的 API 文档调试。

README 只记录当前仓库可直接核对的能力；更细的设计约束和模块说明见 `docs/`、`pkg/*/README.md` 以及各目录下的 `AGENTS.md`。

## 技术栈

- Python `3.14.x`
- `uv`
- FastAPI + Starlette
- Pydantic v2 + `pydantic-settings`
- SQLAlchemy 2.x Async ORM
- Alembic（PostgreSQL schema 版本管理）
- Redis
- Celery + Beat
- anyio
- Loguru
- OpenAI Responses API LLM client（另提供可选 Chat Completions 客户端）
- 向量后端：`pymilvus`

## 当前能力

- 进程入口：`entrypoints/api.py` 导出 FastAPI `app`，`entrypoints/celery_app.py` 为 Worker / Beat 导出
  Celery `celery_app`；应用装配分别位于 `internal/app.py` 和 `internal/infra/celery/application.py`。
- 配置加载：`APP_ENV` 只在根目录 `.env` 维护；应用据此加载 `configs/app.{APP_ENV}.toml` 和
  `.secrets`，并严格隔离非敏感配置与凭据字段。
- 数据库：仅支持 PostgreSQL 与 SQLite。PostgreSQL 用于部署、Alembic 迁移和强并发，可配置只读副本；
  SQLite 用于单元测试和轻量本地存储，不提供行锁、只读副本或 PostgreSQL Celery 状态机。
- Redis：连接池、业务缓存封装、认证 token metadata、Agent action confirmation / idempotency 缓存。
- 认证：Token 认证、内部签名认证、匿名路由分组、微信登录接入点。
- 中间件：请求日志、CORS、Endpoint Guard、统一异常处理、GZip。
- Agent：通用结构化 ReAct / Tool Calling 执行循环，统一 Router Agent，订单售后 Agent，支付支持 Agent；受管理 run 支持 checkpoint 持久化、协作式打断与原 run 恢复（见 [受管理运行使用说明](docs/agent/managed_run_lifecycle.md)）。
- Celery：Worker、Beat、任务路由和示例任务骨架。
- 向量检索：通用 repository / backend 抽象，包含 Milvus backend。
- 基础包：日志、数据库、OSS / S3、第三方登录、加解密、通用 toolkit。

## 环境要求

- Python `3.14.x`
- `uv`
- Redis
- PostgreSQL（当前依赖默认包含 `asyncpg`，仓库配置示例也默认使用 PostgreSQL）

配置层仅接受 `postgresql`、`sqlite` 两种 `db.type`，依赖已包含 `asyncpg` 和 `aiosqlite`。
SQLite 的配置与能力边界见 [数据库迁移指南](docs/development/database_migrations.md#sqlite-轻量本地存储)。

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

复制部署参数和密钥模板：

```bash
cp .env.compose.example .env
cp .secrets.example .secrets
```

启动时配置加载顺序如下：

1. Compose 把根目录 `.env` 中的 `APP_ENV` 注入容器；本地直接运行时应用会直接读取该文件。
2. 加载 `configs/app.{APP_ENV}.toml`。
3. 加载只包含凭据字段的 `.secrets`；该文件不能覆盖环境配置中的普通参数。
4. 除显式注入的 `APP_ENV` 外，不从 shell 环境变量读取应用配置。

缺少根目录 `.env` 中的 `APP_ENV`、缺少 `.secrets`、配置文件缺少必填字段、字段位于错误文件，或出现
未知字段时，应用都会在启动阶段失败。

最小本地配置示例：

`.env`

```env
APP_ENV=local
```

`.secrets`

```env
AES_SECRET=your_aes_secret_key
JWT_SECRET=your_jwt_secret_key
DB_PASSWORD=your_db_password
REDIS_PASSWORD=
WECHAT_APP_ID=
WECHAT_APP_SECRET=
LLM_MIMO_API_KEY=your_mimo_api_key
LLM_DEEPSEEK_API_KEY=your_deepseek_api_key
EMBEDDING_API_KEY=
```

非敏感配置直接使用仓库提供的 `configs/app.local.toml`。该文件中的必填 key 是完整配置契约，调整值即可，
不要删除 key；条件字段只有在对应能力启用时才需要填写。TOML 使用原生布尔、整数和数组，例如
`debug = true`、`db.port = 5432`、`cors.origins = ["*"]`。

配置说明：

- 仓库已提供 `configs/app.local.toml`、`configs/app.dev.toml`、`configs/app.test.toml`、`configs/app.prod.toml`。
- `.secrets` 被 `.gitignore` 忽略，不要提交真实密钥、数据库口令、token 或生产配置。
- `.secrets` 中的 `DB_PASSWORD`、`DB_READ_PASSWORD`、`REDIS_PASSWORD` 支持 `ENC(...)`，运行时使用 `AES_SECRET` 解密。
- 设置 `db.read_replica.host` 后会初始化只读副本；未设置时读会话自动 fallback 到主库。
- `config_echo.reveal_secrets=true` 会在启动配置回显中显示敏感值，只应临时用于本地诊断。
- Logger 只负责控制台输出和向 `log.base_dir/app.log` 追加日志，不在应用内执行轮转、保留或压缩。
- `log.format="json"` 使用 Loguru 原生 `serialize=True`，输出 `text` / `record` 结构；trace ID 位于
  `record.extra.trace_id`，字段说明和旧格式迁移见[日志格式](docs/logging/log_rotation.md#日志格式)。
- 业务日志上下文通过 `logger.bind(...)` 添加到 `record.extra`；TEXT 模式只展示固定字段和消息，
  不自动追加业务上下文。
- Logger 默认只输出到控制台；各环境的日志输出目标统一在 `configs/app.{APP_ENV}.toml` 中显式配置。各部署
  形态的 `log.write_to_file` / `log.write_to_console` 取值见
  [`docs/logging/log_rotation.md`](docs/logging/log_rotation.md#输出目标的选择原则)。
- 启用 `log.write_to_file=true` 的长期运行环境必须同时配置外部日志轮转；部署方案见 `docs/logging/log_rotation.md`。
- `llm.*` 用于 Chat / Agent 推理模型；`embedding.*` 用于向量模型，两者不要共用配置。
- 当前 Router、Agent、RAG 统一使用 `OpenAIResponsesClient`，请求 `/responses`；配置的服务和模型必须支持 Responses API。客户端用法及输出策略见 [LLM 客户端](pkg/llm/README.md)。
- `embedding.provider` 当前支持 `openai_compatible`，可接 OpenAI-compatible `/embeddings` 协议的 BGE、vLLM、Xinference、TEI 或自建向量服务。
- `embedding.dimension` 必须与向量模型输出维度和向量 collection schema 保持一致。
- `endpoint_guard.rules` 是 TOML 数组，可按 method、path、match type 禁用或拒绝指定 endpoint。

### 3. 初始化全新 PostgreSQL 数据库

先创建空数据库，再让 `configs/app.{APP_ENV}.toml` 的 `[db]` 和 `.secrets` 的 `DB_PASSWORD` 指向它。
在仓库根目录执行：

```bash
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

Alembic 创建当前全部业务表并通过 `alembic_version` 记录版本。迁移是独立步骤，API 和 Worker
启动时不自动执行。当前仅支持 PostgreSQL 新库初始化；已有数据库暂不接管，不应直接执行 initial revision
或用 `stamp head` 跳过结构差异。

后续结构变更、离线 SQL 与隔离数据库验证见 [数据库迁移指南](docs/development/database_migrations.md)。

### 4. 启动 API

开发模式：

```bash
uv run uvicorn entrypoints.api:app --reload --host 0.0.0.0 --port 8000 \
  --log-config entrypoints/uvicorn_logging.json
```

非热重载模式：

```bash
uv run uvicorn entrypoints.api:app --host 0.0.0.0 --port 8000 \
  --log-config entrypoints/uvicorn_logging.json
```

`--log-config` 让 Uvicorn 的启动、停止、异常和 access log 复用应用的 Loguru 输出配置，
包括 reload 父进程；详见[日志部署文档](docs/logging/log_rotation.md#uvicorn-日志接入)。

当 `DEBUG=true` 时，FastAPI 文档可访问：

- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/redoc`

### 5. 启动 Celery

Worker：

```bash
uv run celery -A entrypoints.celery_app:celery_app worker -l info --pool prefork
```

未指定 `-Q/--queues` 时，Worker 消费应用 `task_queues` 中的全部队列。队列列表从
`internal/tasks/scheduler.py` 的默认队列、任务路由和 Beat 投递配置派生并去重，维护队列名称来自
环境配置中的 `celery.reconciler_queue`。修改维护队列后需重启相关 Worker 和 Beat，使投递与订阅一致。

启动前先按上面的步骤执行 `alembic upgrade head`。API 先登记 `SUBMITTING` 记录，再调用 `CeleryClient.submit()`；Worker 可从
`SUBMITTING` 或 `QUEUED` claim，从而避免快速消费竞态。Beat 会分别收敛发布确认超时、排队启动超时和
执行 deadline 超时；运行中任务超过 hard deadline 后先进入 `ORPHANED`，等待 fence 过期和业务核对后再进入
`FAILED/CANCELLED`，不会自动重新提交任务。运行中任务可通过 `cancel_allowed=false` 进入不可取消阶段。取消接口先持久化
`CANCELLED/CANCELLING`，再 best-effort
调用非强制 Celery revoke，运行中任务通过数据库检查点协作退出。

状态机相关配置：

- `celery.queue_start_timeout_seconds`：消息确认入队后允许等待 Worker claim 的时间，默认 300 秒。
- `celery.execution_deadline_grace_seconds`：task time limit 之外的状态收敛宽限，默认 30 秒。
- `celery.orphan_fence_seconds`：`ORPHANED` 状态外部副作用隔离窗口，默认 300 秒。
- `celery.publish_confirm_timeout_seconds`：`SUBMITTING` 发布确认超时。
- `celery.publish_reconciler_interval_seconds`：发布确认 Reconciler 周期。
- `celery.reconciler_interval_seconds` / `celery.reconciler_batch_size`：deadline Reconciler 周期与批量。
- `celery.reconciler_queue`：Reconciler 专用 queue。

Beat：

```bash
uv run celery -A entrypoints.celery_app:celery_app beat -l info
```

也可以使用脚本启动 Worker：

```bash
./entrypoints/run_celery_worker.sh
```

脚本支持：

- `CELERY_LOG_LEVEL`
- `CELERY_CONCURRENCY`
- `CELERY_QUEUES`：显式覆盖订阅队列；未设置或为空时使用应用配置。

脚本末尾的命令行参数继续传给 Celery，显式 `-Q/--queues` 优先于 `CELERY_QUEUES`。例如：

```bash
./entrypoints/run_celery_worker.sh --queues=custom_maintenance --max-tasks-per-child=1000
```

为专用 Worker 指定队列时，需确保其他 Worker 覆盖剩余业务和维护队列；上述示例中的队列名应与环境配置一致。

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

实验性请求校验、异常注入、后台任务和 SSE 路由由独立 demo app 提供，不随常规 API 启动；
启动命令与原路径见 [API 演示说明](examples/api/README.md)。

认证规则：

- `/v1/public/**` 直接放行。
- `/v1/internal/**` 通过 Router dependency 使用 `X-Signature`、`X-Timestamp`、`X-Nonce` 做签名认证。
- 其他 `/v1/**` 业务接口通过 Router dependency 校验 `Authorization` token，兼容裸 token 和
  `Bearer <token>`。
- `/v1/auth/login`、`/v1/auth/register`、`/v1/auth/wechat/login` 挂载在匿名 Router；调试模式下的
  `/docs`、`/redoc`、`/openapi.json` 不经过业务 Router 认证。

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
该路线尚未落地到代码，详细方案见 `docs/agent/agentscope_service_integration.md`。

相关文档：

- `docs/agent/agentscope_service_integration.md`
- `pkg/agents/README.md`
- `internal/agents/order/README.md`
- `internal/agents/payment/README.md`

## 项目结构

- `entrypoints/`：进程入口，包含 FastAPI 入口、Worker / Beat 共用的 Celery 入口，以及 API 和 Worker 启动脚本。
- `internal/`：业务应用代码，包含路由、中间件、配置、服务、DAO、缓存、模型、Schema、任务和业务 Agent。
- `internal/services/agents/`：`conversation.py` 为会话用例入口，`storage.py` 统一持有数据库事务，
  `runtime.py` 适配执行协议，`execution.py` 编排受管理运行。
- `internal/schemas/agent/`：按聊天、受管理运行、流事件和 JSON 值分组，包入口保留公开 schema/DTO 导入。
- `pkg/`：可复用基础包，包含通用 Agent、数据库、日志、OSS、第三方登录、加解密、toolkit 和向量检索抽象。
- `examples/`：可运行示例代码，不作为生产运行时基础包。
- `configs/`：非敏感 TOML 环境配置。
- `docs/`：补充文档。
- `tests/`：pytest 测试。
- `migrations/`、`alembic.ini`：PostgreSQL schema 迁移历史与迁移配置。

更细的目录职责和修改约束见各目录下的 `AGENTS.md`；模块级使用说明优先看对应 `README.md`。

## 向量检索

通用向量抽象位于 `pkg/vectors/`，当前 backend：

- `pkg.vectors.backends.milvus`

接入 RAG、知识库检索或混合检索前，先读：

- `pkg/vectors/AGENTS.md`
- `pkg/vectors/backends/milvus/README.md`

## 测试与质量检查

测试使用真实的 `pkg.logger`、`pkg.request_context` 和 `pkg.ids`；根 fixture 按测试隔离日志输出与
请求上下文，API 用例在局部 `conftest.py` 中提供测试身份。需要替身时使用局部 fixture、`monkeypatch`
或 FastAPI `dependency_overrides`，不在收集阶段覆盖 `sys.modules`，也不根据 pytest 命令行选择模块实现。

运行不依赖外部服务的回归：

```bash
uv run pytest -m "not integration"
```

仅检查全量收集可使用 `uv run pytest --collect-only -q`；外部服务及测试目标准备完毕后，再使用
`uv run pytest` 运行包含 integration 的全部用例。

测试默认使用 pytest 的系统临时目录。需要保留或查看测试产物时，可指定仓库内目录：

```bash
uv run pytest --basetemp=.pytest-tmp
```

`.pytest-tmp/` 已加入 `.gitignore`；使用该参数时，目录会在每次运行首次访问 `tmp_path` 时被清空。

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
  -e APP_ENV=local \
  -v "$PWD/.secrets:/app/.secrets:ro" \
  -v "$PWD/configs/app.local.toml:/app/configs/app.local.toml:ro" \
  agent-backend \
  /app/entrypoints/run_api.sh --workers 1
```

镜像不声明默认启动命令，`docker run` 和 Compose 均显式调用 `entrypoints/run_api.sh`。脚本使用镜像已有的
Python 环境启动 Uvicorn，默认监听 `0.0.0.0:8000`，使用 `uvloop` 和 `httptools` 并开启 access log。
脚本通过 `entrypoints/uvicorn_logging.json` 将 Uvicorn 日志转发到应用的 Loguru 输出目标。
额外命令行参数原样透传给 Uvicorn，最后通过 `exec` 替换 Shell 进程。

如果容器内无法访问宿主机服务，需要把 `db.host`、`redis.host` 等配置改成容器网络可解析的地址。

### 生产 Compose 与 journald

`compose.yaml` 使用 Docker journald 驱动，要求目标 Linux 主机运行 systemd-journald 并启用持久化。
应用在 `configs/app.prod.toml` 中关闭文件输出、开启 JSON 控制台日志；不再部署 logrotate sidecar 或挂载应用日志目录。
API 支持 `API_WORKERS` 配置，默认 1；Compose 将其展开为启动脚本的 `--workers` 参数，不注入应用环境。
多 worker 数量结合 CPU、内存、下游连接预算和压测结果调整。

日志标签为 `<COMPOSE_PROJECT_NAME>-<APP_ENV>-api`，默认 `agent-backend-prod-api`。项目名跨发布保持不变，
容器 ID 或镜像更新不改变标签。宿主机 journal 独立保留历史，可按需导出 `.log`，不提供跨主机备份或零丢失保证。

新部署先参考 [日志部署文档](docs/logging/log_rotation.md#docker-compose-journald) 完成宿主机准备、
密钥权限检查与依赖配置，再执行：

```bash
cp .env.compose.example .env
docker compose config --quiet
docker compose build api
docker compose up -d api
```

已有 `.env` 应手动合并，勿直接覆盖。宿主机配置模板位于 `deploy/journald/agent-backend.conf`，
不会由 Compose 自动安装；磁盘预算与保留策略影响整机 journal，需要运维确认后应用。

```bash
# 日常查看；标签按实际项目名和环境替换
sudo journalctl CONTAINER_TAG=agent-backend-prod-api -n 200 -f -o cat

# 跨发布查询，包含尚未过期的旧容器日志
sudo journalctl CONTAINER_TAG=agent-backend-prod-api --since "1 day ago" --no-pager -o cat
```

持久化路径、容量与限流、按时间导出和下载、旧文件日志保留及回滚见
[日志部署文档](docs/logging/log_rotation.md#docker-compose-journald)。
当前 Compose 固定发布宿主机端口，多容器扩容需要额外配置端口与负载均衡。

仅在获准的 Linux 测试主机执行隔离验收：

```bash
./scripts/verify_journald.sh /tmp/agent-backend-journald-acceptance
```

脚本验证多 worker 输出、固定标签、容器重建和删除后的历史查询及持久化日志导出；不修改宿主机配置，
不删除旧部署日志。宿主机重启、长期保留和高峰日志完整性仍需另行验收。

## 相关文档

- `docs/README.md`
- [AnyIO 线程任务超时与取消语义](pkg/concurrency/README.md)
- `pkg/agents/README.md`
- `pkg/vectors/backends/milvus/README.md`

## License

MIT
