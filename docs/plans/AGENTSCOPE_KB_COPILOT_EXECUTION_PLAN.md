# AgentScope 2.0 Agent Service 接入执行计划

状态：待实施。当前仅完成文档路线同步，尚未修改 FastAPI 挂载、配置、测试或运行时代码。

本文档替代旧版 `AgentScope KB Copilot` 自建接口路线。新的结论是：当前接入方向不应手写一套
`/kb-copilot/chat`、`/chat/stream`、agent/session/pending-action 适配层；应参考官方
AgentScope 2.0 Agent Service 文档，把官方 FastAPI app 嵌入到本项目中，并选择本地文件系统
workspace 后端。

官方参考：

- Agent Service 文档：https://docs.agentscope.io/zh/v2/deploy/agent-service
- AgentScope 2.0 文档首页：https://docs.agentscope.io/zh/v2
- AgentScope GitHub：https://github.com/agentscope-ai/agentscope

## 1. 关键结论

- 使用 `agentscope.app.create_app(...)` 创建 AgentScope service 子应用。
- 使用 `RedisStorage` 保存 Agent、Session、Credential、Message、Schedule 等 AgentScope 资源。
- 使用 `RedisMessageBus` 作为 session lock、replay log、inbox queue、wakeup signal 的运行时消息总线。
- 使用 `LocalWorkspaceManager` 作为“嵌入到自己的代码 - 本地文件系统”的 workspace 实现。
- 本地文件系统只表示 Agent 工具执行的 workspace 目录在本机目录中管理；不表示 AgentScope 的全部状态都改成本地文件存储。
- 不再把首版目标定义为项目自有 `/v1/agentscope/kb-copilot/chat` 和 `/chat/stream` API。
- 不再维护项目自造的 AgentScope event -> 自定义 KB Copilot SSE 协议作为主线。
- AgentScope service 的官方 REST API 是主 contract，包括 `/agent`、`/credential`、`/sessions`、`/chat`、
  `/sessions/{session_id}/stream`、`/workspace/mcp`、`/workspace/skill`、`/schedule` 等。

## 2. 推荐挂载形态

将 AgentScope service 作为 FastAPI mounted sub-application 挂到主应用下：

```text
/v1/agentscope/*
```

推荐原因：

- 继续复用本项目 `/v1` 默认 token 认证边界。
- 不污染现有项目 `BaseResponse[T]` 业务 API contract；AgentScope 子应用保留官方 contract。
- AgentScope app 自带 lifespan，但 FastAPI mounted sub-application 的 lifespan 不会自动执行；主应用
  lifespan 必须显式进入 AgentScope app 的 lifespan，才能管理 storage、message bus、workspace manager、
  scheduler 和 chat service。
- 后续可以独立替换 AgentScope auth、credential、provider、middleware、workspace manager。

实现草图：

```python
from agentscope.app import create_app as create_agentscope_app
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager

storage = RedisStorage(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    password=settings.REDIS_PASSWORD.get_secret_value() or None,
)
message_bus = RedisMessageBus(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    password=settings.REDIS_PASSWORD.get_secret_value() or None,
)
workspace_manager = LocalWorkspaceManager(
    basedir=str(settings.AGENTSCOPE_WORKSPACE_BASEDIR),
    ttl=settings.AGENTSCOPE_WORKSPACE_TTL_SECONDS,
)

agentscope_app = create_agentscope_app(
    storage=storage,
    message_bus=message_bus,
    workspace_manager=workspace_manager,
    title="Agent Backend AgentScope",
)
app.mount("/v1/agentscope", agentscope_app)
```

最终代码应放到项目 infra/provider 中，而不是堆在 `internal/app.py`。

## 3. 与项目认证的关系

AgentScope 官方默认使用 `X-User-ID` header 作为占位鉴权。接入本项目时不能让客户端伪造
`X-User-ID` 成为安全边界。

目标行为：

- 外层主应用继续执行 `ASGIAuthMiddleware`。
- `/v1/agentscope/*` 仍需要项目 token。
- AgentScope 内部 `get_current_user_id` 依赖应覆盖为读取项目上下文中的 `pkg.request_context.get_user_id()`。
- 返回给 AgentScope 的 user id 使用字符串形式，例如 `str(get_user_id())`，以匹配 AgentScope 多租户资源模型。

实现时需要验证：

- mounted sub-app 是否会完整经过主应用中间件。
- dependency override 是否准确覆盖 `agentscope.app.deps.get_current_user_id`。
- 未带 token 访问 `/v1/agentscope/chat` 应返回项目统一未授权错误，而不是接受 `X-User-ID`。
- 带 token 访问时，即使传入伪造 `X-User-ID`，AgentScope 资源归属也应使用项目 token 中的 user id。

## 4. 配置计划

新增配置建议：

```python
AGENTSCOPE_SERVICE_ENABLED: bool = False
AGENTSCOPE_SERVICE_PREFIX: str = "/v1/agentscope"
AGENTSCOPE_WORKSPACE_BASEDIR: str = "runtime/agentscope/workspaces"
AGENTSCOPE_WORKSPACE_TTL_SECONDS: float = 3600.0
```

Redis 默认复用项目 `REDIS_HOST`、`REDIS_PORT`、`REDIS_PASSWORD`、`REDIS_DB`。如果后续要避免 key
混用，再新增独立配置：

```python
AGENTSCOPE_REDIS_DB: int | None = None
AGENTSCOPE_STORAGE_KEY_TTL_SECONDS: int | None = None
```

本地 workspace 目录要求：

- 运行时自动创建目录。
- 不提交 workspace 运行数据。
- 目录应在 `.gitignore` 覆盖范围内，例如新增 `runtime/`。
- 不把 workspace 目录作为静态资源暴露。

## 5. 需要新增或调整的文件

优先修改：

```text
internal/infra/agentscope_service.py
internal/app.py
internal/config.py
tests/api/test_agentscope_service_mount.py
README.md
docs/README.md
```

说明：`.gitignore` 已覆盖 `runtime/`，本轮无需额外修改。

删除或降级旧路线文件：

```text
internal/controllers/api/agentscope_kb.py
internal/schemas/agentscope_kb.py
internal/services/agentscope_kb/
internal/services/dto/agentscope_kb.py
internal/services/kb/
internal/agents/agentscope_kb/
tests/api/test_agentscope_kb.py
tests/services/test_agentscope_kb.py
tests/agents/test_agentscope_kb_tools.py
```

处理策略：

- 如果这些文件尚未合并到主线，建议删除旧 demo 实现，避免两个 AgentScope contract 并存。
- 如果需要保留旧 KB Copilot 思路，应改为“基于 AgentScope Agent Service 的示例配置/seed 数据/skill”，而不是
  自建同名 chat API。
- 旧 KB Copilot 设计文档已选择性合并到 `docs/agent/agentscope_service_integration.md`，并从文档集中删除。
- 删除旧文件前必须确认没有其他业务入口依赖它们。

## 6. 分阶段执行

### Phase 0: 基线确认

目标：确认当前依赖、官方 API、项目中间件和旧实现影响面。

执行项：

- 检查 `agentscope` 当前锁定版本及实际导出路径。
- 用本地 Python introspection 确认以下签名：
  - `agentscope.app.create_app`
  - `RedisStorage`
  - `RedisMessageBus`
  - `LocalWorkspaceManager`
  - `agentscope.app.deps.get_current_user_id`
- 检查主应用 mounted sub-app 是否经过项目认证、日志和异常中间件。
- 检查旧 `agentscope_kb` router 是否已被 `internal/controllers/api/__init__.py` 引入。

完成标准：

- 明确是否删除旧 router。
- 明确 AgentScope service mount prefix。
- 明确 workspace 本地目录。

建议验证：

```bash
git status --short
uv run python - <<'PY'
import inspect
from agentscope.app import create_app
from agentscope.app.storage import RedisStorage
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.workspace_manager import LocalWorkspaceManager
print(inspect.signature(create_app))
print(inspect.signature(RedisStorage))
print(inspect.signature(RedisMessageBus))
print(inspect.signature(LocalWorkspaceManager))
PY
```

### Phase 1: 嵌入官方 AgentScope Service

目标：在本项目 FastAPI 应用中挂载官方 AgentScope service，并使用本地文件系统 workspace。

实现要点：

- 新增 `internal/infra/agentscope_service.py`，封装 AgentScope app 创建逻辑。
- `internal/app.py` 根据 `settings.AGENTSCOPE_SERVICE_ENABLED` 决定是否 mount。
- `internal/app.py` 在主应用 lifespan 中显式进入 mounted AgentScope app 的 lifespan；不要假设 FastAPI 会自动
  执行 mounted sub-app 的 lifespan。
- `LocalWorkspaceManager.basedir` 使用配置目录，并在创建 app 前确保目录存在。
- `RedisStorage` 和 `RedisMessageBus` 使用项目 Redis 配置。
- 覆盖 AgentScope `get_current_user_id` 依赖，接入项目 token 上下文。
- 不把 AgentScope 子应用响应包成 `BaseResponse[T]`。
- 不在主项目 controller 中重写 AgentScope 的 `/chat` 或 `/sessions/{id}/stream`。

完成标准：

- 开启配置后，主应用存在 `/v1/agentscope/*` 路由。
- AgentScope OpenAPI 或路由表能看到官方 Agent Service endpoints。
- 主应用启动时 AgentScope storage、message bus、workspace manager 的 lifespan 被进入，关闭时按相反顺序退出。
- 未认证请求不能访问 `/v1/agentscope/*`。
- 已认证请求不会信任客户端传入的 `X-User-ID`。

### Phase 2: 清理旧 KB Copilot 自建接口

目标：移除或降级旧的自造 KB Copilot API，避免和官方 Agent Service 并行产生错误示范。

执行项：

- 从 `internal/controllers/api/__init__.py` 移除 `agentscope_kb.router`。
- 删除旧 `/v1/agentscope/kb-copilot/chat` 与 `/chat/stream` controller/schema/service/tests。
- 如需保留知识库运维示例，将其重新设计为 AgentScope service 中的 Agent 配置、tool/skill 或 seed 流程。
- 保持 `docs/agent/agentscope_service_integration.md` 为唯一 AgentScope 2.0 主接入方案文档。

完成标准：

- API contract 中不再出现自造 `kb-copilot` chat contract。
- 测试不再依赖旧 AgentScope runtime adapter。
- 文档不再要求实现 `internal/agents/agentscope_kb/builder.py` 这类手写 runtime glue 作为主线。

### Phase 3: Provider、Credential 与模型接入

目标：让 AgentScope service 能通过官方 credential/model 机制使用项目已有 LLM provider。

执行项：

- 先使用 AgentScope 内置 provider 验证最小闭环。
- 如果要接入项目现有 LLM 配置，定义 `CredentialBase` 子类和 `ChatModelBase` adapter。
- 通过 `create_app(extra_credentials=[...])` 注册额外 credential。
- 不把项目真实 provider API key 写死在代码或 seed 数据中。

完成标准：

- `GET /v1/agentscope/credential/schemas` 可发现可用 credential schema。
- `GET /v1/agentscope/model?provider=<name>` 可返回模型卡片。
- 凭证保存、会话创建、聊天触发和 SSE stream 可完成官方流程。

### Phase 4: KB 运维能力重新建模

目标：如果仍要做知识库运维 Copilot，把它放到 AgentScope service 的官方资源模型中，而不是自造旁路 API。

可选路线：

- 通过 `extra_agent_tools` 注入项目 KB 查询、诊断、索引任务准备等 tool。
- 通过 workspace skill 机制加载 KB 运维 skill。
- 使用项目 Service 作为 tool 的业务边界，tool 不直接访问 DAO、Redis、Milvus、Celery。
- 有副作用操作仍走项目 Service 的 pending action / confirm 机制，不能只依赖 LLM 或 tool prompt。

首版不做：

- 真实文件上传与解析 pipeline。
- 真实 Milvus upsert/delete。
- 批量重建全部 KB。
- 复杂 RBAC、审批流、跨环境发布。

## 7. 测试矩阵

挂载与配置：

- `AGENTSCOPE_SERVICE_ENABLED=false` 时不挂载 `/v1/agentscope`。
- `AGENTSCOPE_SERVICE_ENABLED=true` 时挂载官方子应用。
- workspace basedir 自动创建，并且不会写入仓库受版本管理的数据目录。

认证：

- 无 token 访问 `/v1/agentscope/chat` 被项目认证拒绝。
- 有 token 时 AgentScope user id 来自项目上下文。
- 伪造 `X-User-ID` 不改变资源归属。

AgentScope service contract：

- 官方 `/agent`、`/credential`、`/sessions`、`/chat`、`/sessions/{id}/stream` 路由存在。
- 子应用响应保持 AgentScope 官方结构，不被项目 `BaseResponse[T]` 包装。
- lifespan 能正确进入和退出 storage、message bus、workspace manager。

旧实现清理：

- `rg "kb-copilot|agentscope_kb" internal tests docs` 只保留明确标注为历史或迁移说明的内容。
- `internal/controllers/api/__init__.py` 不再引入旧 router。

## 8. 建议验证命令

```bash
uv run pytest tests/api/test_agentscope_service_mount.py -q
uv run ruff check internal/infra/agentscope_service.py internal/app.py internal/config.py tests/api/test_agentscope_service_mount.py
```

如果删除旧实现，再运行：

```bash
uv run pytest tests/api tests/services tests/agents -q
uv run ruff check internal tests
```

如果执行真实 AgentScope chat 流程，需要本地 Redis 可用，并准备可用模型凭证；否则只做路由、认证和依赖覆盖层面的单元测试。

## 9. 风险与控制

| 风险 | 控制 |
| --- | --- |
| 把 `LocalWorkspaceManager` 误解为全量本地持久化 | 文档和配置明确：资源状态仍由 `RedisStorage` 管理 |
| 客户端伪造 `X-User-ID` | 覆盖 AgentScope user dependency，只读项目 token 上下文 |
| mounted sub-app 绕过认证 | 测试覆盖 `/v1/agentscope/*` 未认证请求 |
| mounted sub-app lifespan 不自动执行 | 主应用 lifespan 显式进入 AgentScope app lifespan，测试覆盖进入与退出 |
| 官方子应用 lifespan 与主应用 lifespan 冲突 | 只进入 AgentScope app 公开 lifespan context，避免手动调用内部 manager |
| 旧 KB Copilot API 与官方 API 并存 | Phase 2 删除旧 router 或明确降级为历史设计 |
| workspace 文件泄露或入库 | 使用 ignored runtime 目录，不暴露静态文件，不记录完整文件内容 |
| 复用 Redis DB 造成 key 混用 | 首版复用项目 Redis；如出现隔离需求，新增独立 Redis DB/TTL 配置 |

## 10. 退出准则

本轮接入完成的判断标准：

- 项目可配置化挂载 AgentScope 2.0 官方 Agent Service。
- AgentScope service 使用 `RedisStorage`、`RedisMessageBus`、`LocalWorkspaceManager`。
- `/v1/agentscope/*` 经过项目 token 认证，并把项目 user id 注入 AgentScope 多租户模型。
- 旧 `/v1/agentscope/kb-copilot/*` 自造接口不再作为主线 contract。
- 文档、测试和 README 都描述“嵌入官方 Agent Service + 本地文件系统 workspace”的路线。
