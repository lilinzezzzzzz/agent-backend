# AgentScope 2.0 Agent Service 接入方案

当前项目接入 AgentScope 2.0 的主线是嵌入官方 Agent Service，而不是自建
`/kb-copilot/chat`、`/chat/stream`、自定义 SSE 协议或单独的 AgentScope runtime glue。

状态：文档方案已确定，代码暂未实施。

官方参考：

- Agent Service 文档：https://docs.agentscope.io/zh/v2/deploy/agent-service
- AgentScope 2.0 文档首页：https://docs.agentscope.io/zh/v2
- AgentScope GitHub：https://github.com/agentscope-ai/agentscope

## 1. 结论

- 使用 `agentscope.app.create_app(...)` 创建 AgentScope FastAPI 子应用。
- 将子应用 mount 到主应用的 `/v1/agentscope` 前缀下。
- 使用 AgentScope 官方 REST / SSE contract，不包项目 `BaseResponse[T]`。
- 使用 `RedisStorage` 保存 AgentScope 的 Agent、Session、Credential、Message、Schedule 等资源。
- 使用 `RedisMessageBus` 承载 session lock、replay log、inbox queue、wakeup signal 等运行时能力。
- 使用 `LocalWorkspaceManager` 管理本地文件系统 workspace，默认建议位于 `runtime/agentscope/workspaces`。
- 覆盖 AgentScope 默认 `X-User-ID` 依赖，改为读取项目 token 认证后写入的 user context。
- 旧 KB Copilot 设计中的知识库运维能力只保留为 Agent Service 里的 Agent 配置、tool、skill 或 seed 数据思路。

不再作为主线的内容：

- 不新增 `internal/controllers/api/agentscope_kb.py` 这类自造 chat controller。
- 不新增 `/v1/agentscope/kb-copilot/*` 作为首版 HTTP contract。
- 不把 AgentScope event 重新映射为项目自定义 SSE 协议作为主路径。
- 不让 AgentScope tool 直接访问 ORM、Redis、Milvus、Celery 或 mock 常量。

## 2. 项目约束

当前项目已有以下边界，AgentScope Service 接入必须复用或避开：

- 进程入口由 `entrypoints/main.py` 导出 `internal.app.create_app()` 创建的 FastAPI app。
- `internal/app.py` 负责路由、中间件和 lifespan；基础设施初始化发生在主应用 lifespan。
- `/v1` 默认 token 认证，`/v1/public` 无认证，`/v1/internal` 签名认证。
- 配置由 `internal/config.py` 管理，先读取 `configs/.secrets` 中的 `APP_ENV`，再加载
  `configs/.env.{APP_ENV}`，最后由 `configs/.secrets` 覆盖同名配置。
- `.secrets` 用于 `APP_ENV` 和敏感值，例如 `AES_SECRET`、`JWT_SECRET`、`DB_PASSWORD`、
  `REDIS_PASSWORD`、LLM provider API key；文档和日志不能泄露真实值。
- 当前配置回显会按 `.secrets` 中的敏感 key 做掩码；`CONFIG_ECHO_REVEAL_SECRETS=true`
  只允许本地临时诊断。
- Redis 已用于 token metadata、业务缓存、Endpoint Guard 动态规则等能力；AgentScope 复用 Redis
  时需要明确 DB 和 key TTL 策略。
- `runtime/` 已被 `.gitignore` 忽略，适合作为本地 workspace 运行目录。

## 3. 推荐挂载形态

目标挂载：

```text
/v1/agentscope/*
```

推荐原因：

- 继续使用项目 `/v1` 默认 token 认证边界。
- 不污染现有业务 API 的 `BaseResponse[T]` 响应信封。
- AgentScope 官方 OpenAPI、REST resource、SSE stream 和前端协议保持一致。
- 后续可单独替换 AgentScope 的 credential、provider、workspace manager 和 tool factory。

实现时在 `internal/app.py` 中新增独立注册函数，例如：

```python
def register_agentscope_service(app: FastAPI) -> None:
    if not settings.AGENTSCOPE_SERVICE_ENABLED:
        return

    from internal.infra.agentscope_service import create_agentscope_service_app

    agentscope_app = create_agentscope_service_app()
    app.mount(settings.AGENTSCOPE_SERVICE_PREFIX, agentscope_app)
    app.state.agentscope_service_app = agentscope_app
```

注意：FastAPI mounted sub-application 的 lifespan 不会自动由父应用进入。主应用 lifespan 必须显式进入
AgentScope app 的 lifespan，避免 storage、message bus、workspace manager、scheduler 或 dispatcher
未启动。

## 4. 生命周期

主应用当前 lifespan 顺序是：

```text
init_settings()
init_logger()
init_async_db()
init_async_redis()
init_signature_auth_handler()
init_snowflake_id_generator()
init_background_task_manager()
yield
close_async_db()
close_async_redis()
close_background_task_manager()
```

接入 AgentScope 后建议：

- 在主应用 `create_app()` 阶段根据配置创建并 mount AgentScope 子应用。
- 在主应用 lifespan 初始化完项目基础设施后，显式进入 mounted AgentScope app 的
  `router.lifespan_context`。
- 使用 `AsyncExitStack` 管理 AgentScope lifespan，确保 AgentScope 内部资源在主应用关闭时先退出。
- 不手动调用 AgentScope 内部 manager 的私有初始化逻辑，只进入官方 FastAPI app lifespan。
- 如果 AgentScope 创建自己的 Redis connection pool，关闭由 AgentScope lifespan 负责；不要复用后又重复关闭项目
  `internal.infra.redis` 的连接池，除非明确传入外部 connection pool 并接管生命周期。

## 5. 配置设计

建议新增配置字段，放在 `Settings` 的 Agent 或 AgentScope 分组中：

```python
AGENTSCOPE_SERVICE_ENABLED: bool = False
AGENTSCOPE_SERVICE_PREFIX: str = "/v1/agentscope"
AGENTSCOPE_WORKSPACE_BASEDIR: str = "runtime/agentscope/workspaces"
AGENTSCOPE_WORKSPACE_TTL_SECONDS: float = 3600.0
AGENTSCOPE_REDIS_DB: int | None = None
AGENTSCOPE_STORAGE_KEY_TTL_SECONDS: int | None = None
```

配置文件落点：

- `AGENTSCOPE_SERVICE_ENABLED`、`AGENTSCOPE_SERVICE_PREFIX`、workspace 路径、TTL、Redis DB
  这类非敏感配置放在 `configs/.env.{APP_ENV}`。
- 真实 API key、token、password 不放在 `.env.{APP_ENV}`，只放 `.secrets` 或由 AgentScope
  官方 `Credential` 资源管理。
- `AGENTSCOPE_REDIS_DB` 不配置时复用 `REDIS_DB`；如果担心 key 混用，使用独立 DB。
- `AGENTSCOPE_STORAGE_KEY_TTL_SECONDS` 默认为 `None`，表示 AgentScope 资源不自动过期；demo
  环境可以设置 TTL，生产环境需要根据会话保留策略单独确认。
- `AGENTSCOPE_SERVICE_PREFIX` 必须以 `/` 开头，建议禁止配置为 `/` 或尾部带 `/`，避免吞掉主应用路由或产生重复路径。

`.env.test` / `.env.local` 建议示例：

```env
AGENTSCOPE_SERVICE_ENABLED=false
AGENTSCOPE_SERVICE_PREFIX=/v1/agentscope
AGENTSCOPE_WORKSPACE_BASEDIR=runtime/agentscope/workspaces
AGENTSCOPE_WORKSPACE_TTL_SECONDS=3600
# AGENTSCOPE_REDIS_DB=
# AGENTSCOPE_STORAGE_KEY_TTL_SECONDS=
```

`.secrets` 建议：

- 保留现有 `APP_ENV`、`AES_SECRET`、`JWT_SECRET`、`DB_PASSWORD`、`REDIS_PASSWORD`、LLM provider
  API key 设计。
- 不新增非敏感 AgentScope 开关到 `.secrets`。
- 如果未来需要全局 AgentScope provider key，应使用明确命名的 secret key，并说明它是系统级凭证；
  多用户凭证优先走 AgentScope 官方 Credential API。

## 6. 认证与权限

### 6.1 外层 HTTP 认证

当前 `ASGIAuthMiddleware` 规则：

- `path.startswith("/v1/public")` 直接放行。
- `path.startswith("/v1/internal")` 使用 `X-Signature`、`X-Timestamp`、`X-Nonce` 签名认证。
- 其他 HTTP 请求默认 token 认证，读取 `Authorization`，兼容裸 token 和 `Bearer <token>`。
- token 校验通过后，认证中间件把 `auth_metadata["id"]` 写入 `pkg.toolkit.context.ContextKey.USER_ID`。

因此 `/v1/agentscope/*` 不应加入白名单，也不应挂载到 `/v1/public` 或 `/v1/internal` 下。

### 6.2 AgentScope user id 注入

AgentScope 官方默认 `X-User-ID` 是占位依赖，不提供真实鉴权。本项目接入时必须覆盖
`agentscope.app.deps.get_current_user_id`：

```python
from agentscope.app.deps import get_current_user_id as agentscope_get_current_user_id
from pkg.toolkit import context


async def get_agentscope_current_user_id() -> str:
    return str(context.get_user_id())


agentscope_app.dependency_overrides[agentscope_get_current_user_id] = get_agentscope_current_user_id
```

安全要求：

- 客户端传入的 `X-User-ID` 不得成为 AgentScope 资源归属依据。
- 未带 token 访问 `/v1/agentscope/*` 应由项目认证中间件拒绝。
- 已认证请求即使带伪造 `X-User-ID`，AgentScope 内部也只能看到项目 token 中的 user id。
- 如果 context 中没有 user id，应返回未授权或服务配置错误，不要 fallback 到 header。

### 6.3 Endpoint Guard

当前项目已有 Endpoint Guard，可按配置或 Redis 动态拒绝 endpoint。AgentScope 接入后应支持：

- 用 prefix rule 禁用 `/v1/agentscope` 整体服务。
- 用 exact / prefix / glob rule 禁用高风险的 credential、workspace 或 schedule endpoint。
- Endpoint Guard 应在进入 AgentScope 子应用前生效，避免被官方子应用路由绕过。

### 6.4 AgentScope permission 不是最终安全边界

AgentScope permission / HITL 只能作为 Agent 行为闸门，不能替代项目 Service 权限校验。

知识库运维 tool 必须遵守：

- tool 只能调用项目 Service 或明确协议，不直接访问 DAO、Redis、Milvus、Celery。
- Service 必须重新校验 `user_id`、KB 可见范围、幂等、任务归属和操作环境。
- 删除、purge、跨租户、批量重建等高风险操作默认不开放给首版 AgentScope tool。
- 如果使用 AgentScope-native HITL，确认恢复流程必须遵守官方 session / event contract；
  不恢复旧设计里的自造 confirm API 作为主线。

## 7. 存储与 workspace

推荐首版：

```text
RedisStorage       -> AgentScope 资源持久化
RedisMessageBus    -> AgentScope 运行时消息总线
LocalWorkspaceManager -> 本地 workspace
```

Redis 设计：

- 默认复用 `REDIS_HOST`、`REDIS_PORT`、`REDIS_PASSWORD`。
- `AGENTSCOPE_REDIS_DB` 未配置时复用 `REDIS_DB`；如果线上 key 隔离要求更高，使用独立 DB。
- AgentScope credential、session、message、schedule 都是持久化资源；不要把它们误认为临时缓存。
- 如果设置 `AGENTSCOPE_STORAGE_KEY_TTL_SECONDS`，要明确会话、凭证、消息和 schedule 过期后的用户影响。

Workspace 设计：

- 默认使用 `runtime/agentscope/workspaces`。
- 启动时自动创建目录。
- 不把 workspace 作为静态资源暴露。
- 不提交 workspace 运行数据。
- 不在日志中输出完整文件内容或敏感路径。
- `LocalWorkspaceManager` 表示工具执行目录在本机文件系统，不表示 AgentScope 全部状态都改成本地文件存储。

## 8. 官方 API 使用方式

实施并启用后，官方 Agent Service endpoints 位于 `/v1/agentscope` 前缀下。主要路径：

```text
POST /v1/agentscope/chat
GET  /v1/agentscope/sessions/{session_id}/stream
GET  /v1/agentscope/sessions/{session_id}/messages
GET/POST/PATCH/DELETE /v1/agentscope/sessions
GET/POST/PATCH/DELETE /v1/agentscope/agent
GET/POST/PATCH/DELETE /v1/agentscope/credential
GET /v1/agentscope/credential/schemas
GET /v1/agentscope/model?provider=<name>
GET/POST/PATCH/DELETE /v1/agentscope/schedule
GET/POST/DELETE /v1/agentscope/workspace/mcp
GET/POST/DELETE /v1/agentscope/workspace/skill
```

典型流程：

```text
1. POST /v1/agentscope/agent
   创建 Agent 记录：名称、system prompt、运行时配置。

2. GET /v1/agentscope/credential/schemas
   获取 provider 凭证 schema。

3. POST /v1/agentscope/credential
   保存当前用户自己的 provider credential。

4. POST /v1/agentscope/sessions
   创建绑定 Agent、model、credential 的 session。

5. POST /v1/agentscope/workspace/mcp 或 /workspace/skill
   可选：为 session workspace 注册 MCP client 或 skill。

6. POST /v1/agentscope/chat
   触发一次 chat run。

7. GET /v1/agentscope/sessions/{session_id}/stream
   订阅官方 session SSE 流，接收事件、回放和后台工具结果。
```

项目业务 controller 不重写这些路径；业务前端应按 AgentScope 官方 contract 调用。

## 9. 知识库运维能力的保留方式

旧 KB Copilot 设计中仍有价值的是“KB 运维能力边界”，但承载方式要切换到 Agent Service 的官方资源模型。

建议保留为 Agent 配置 / tool / skill：

| 能力 | 推荐承载方式 | 边界 |
| --- | --- | --- |
| 列出当前用户可见 KB | `extra_agent_tools` 或 skill | 调用 `KbCatalogService`，不直接读 DAO |
| 获取 KB 概览 | tool / skill | Service 返回 KB、document、chunk、task 摘要 |
| 检索探针 | tool / skill | 通过 `KbDiagnosticService -> RagService` |
| 召回质量诊断 | tool / skill | 汇总 registry 指标和 retrieval evidence |
| 准备重建索引 | tool / skill + permission/HITL | 只生成计划或待确认结果，不直接提交高危任务 |
| 查询任务状态 | tool / skill | 调用项目任务 Service |

首版可开放的 tool：

```text
list_visible_kbs
get_kb_overview
run_retrieval_probe
diagnose_kb_quality
get_index_task_status
```

首版不建议开放：

```text
delete_knowledge_base
purge_vector_collection
delete_orphan_chunks
switch_embedding_model
rebuild_all_kbs
```

如果要支持 `prepare_rebuild_index`：

- 首版只返回诊断结论、重建建议或 AgentScope-native HITL 所需的确认信息。
- 真实任务提交必须由项目 Service 执行资源归属、环境、幂等和审计校验。
- 不恢复旧 `/v1/agentscope/kb-copilot/actions/{id}/confirm` 作为主线。

## 10. RAG / Vector 复用边界

AgentScope tool 不应直接调用 vector repository。默认链路：

```text
AgentScope tool / skill
-> 项目 KB Service
-> RagService.retrieve()
-> ChunkVectorRepository
-> pkg.vectors backend
```

原因：

- `RagService` 已处理 requested scope 与 allowed scope 求交。
- `RagService` 已封装 evidence DTO、引用校验、低置信度和 no evidence 语义。
- `ChunkVectorRepository` 只表达向量检索层能力，不持有完整 KB owner、tenant、parse status、
  index status 或 ACL。

只有在项目 Service 内部做低层诊断时，才允许直接使用 `ChunkVectorRepository`，例如验证 collection
是否存在、scalar filter 是否可用、按 `doc_id` / `kb_id` 做受控查询。即便如此，也必须由 Service
注入 repository，不允许 AgentScope tool 直接 new repository。

## 11. Provider 与 Credential

AgentScope 官方 Agent Service 支持 credential schema 和 provider model card。接入本项目时有两种策略：

### 11.1 推荐：用户级 AgentScope Credential

- 前端通过 `/v1/agentscope/credential/schemas` 获取可用 provider schema。
- 用户通过 `/v1/agentscope/credential` 保存自己的 provider API key。
- Credential 由 AgentScope `RedisStorage` 按 user id 归属管理。
- 项目不把全局 `.secrets` 中的 LLM API key 暴露给用户。

### 11.2 可选：项目全局 LLM provider adapter

仅适用于 demo 或系统级 Agent：

- 复用 `LLM_DEFAULT_PROVIDER`、`LLM_*_BASE_URL`、`LLM_*_MODEL`、`LLM_*_API_KEY`。
- API key 仍放 `.secrets`，不可写入 `.env.{APP_ENV}`、日志、workspace 或 AgentScope message。
- 文档必须说明这是系统级凭证，不代表多租户用户自带 credential。
- 如果 provider adapter 暴露为 AgentScope model card，不得把 secret 作为 schema default 返回给客户端。

## 12. 日志、审计与脱敏

建议记录：

- user_id
- agent_id
- session_id
- chat run id
- requested KB / effective KB scope
- tool name
- tool result 摘要
- permission / HITL decision
- schedule trigger
- error code

不要记录：

- token、API key、credential 明文。
- 完整 Redis URL、数据库连接串明文。
- 大量 chunk 原文。
- workspace 文件完整内容。
- AgentScope raw event 的全部 payload。

如果接入 AgentScope OpenTelemetry：

- 由主应用启动时统一初始化。
- trace id 与项目 `pkg.toolkit.context.get_trace_id()` 关联。
- 不替代项目业务审计。

## 13. 错误处理

AgentScope 子应用使用官方响应结构，不包 `BaseResponse[T]`。但外层认证、Endpoint Guard、请求日志等中间件仍由项目主应用处理。

建议语义：

| 场景 | 处理 |
| --- | --- |
| 未认证访问 `/v1/agentscope/*` | 项目认证中间件返回 `errors.Unauthorized` |
| `X-User-ID` 伪造 | 忽略 header，使用项目 context user id |
| Redis / storage 不可用 | 启动失败或 AgentScope 官方错误，不吞异常 |
| workspace 目录不可创建 | 启动失败 |
| provider credential 缺失 | AgentScope credential/model 流程返回官方错误 |
| KB tool 无权限 | 项目 Service 拒绝，tool 返回受控错误摘要 |
| Endpoint Guard 命中 | 项目统一错误结构，在进入 AgentScope 子应用前返回 |

## 14. 测试与验证

未来实施时至少补充：

- `AGENTSCOPE_SERVICE_ENABLED=false` 时不挂载 `/v1/agentscope`。
- `AGENTSCOPE_SERVICE_ENABLED=true` 时挂载官方子应用。
- 主应用 lifespan 显式进入和退出 AgentScope app lifespan。
- 未带 token 访问 `/v1/agentscope/chat` 被项目认证拒绝。
- 带 token 且伪造 `X-User-ID` 时，AgentScope user id 仍来自项目 token。
- `AGENTSCOPE_SERVICE_PREFIX` 校验：必须以 `/` 开头，不能为 `/`，不能以 `/` 结尾。
- workspace 目录使用 `runtime/` 下路径并自动创建。
- Endpoint Guard 可禁用 `/v1/agentscope` prefix。
- KB tool 只能调用项目 Service，不直接访问 DAO / Redis / Milvus。

建议命令：

```bash
uv run pytest tests/api/test_agentscope_service_mount.py tests/test_config.py -q
uv run ruff check internal/infra/agentscope_service.py internal/app.py internal/config.py tests/api/test_agentscope_service_mount.py tests/test_config.py
```

真实聊天流程额外要求：

- 本地 Redis 可用。
- AgentScope 可用 credential 或项目全局 provider adapter 已配置。
- workspace 目录可写。

## 15. 分阶段落地

### Phase 1：官方 Agent Service 嵌入

- 新增配置字段。
- 新增 `internal/infra/agentscope_service.py`。
- `internal/app.py` 按配置 mount `/v1/agentscope`。
- 主应用 lifespan 显式进入 AgentScope lifespan。
- 覆盖 `get_current_user_id`。
- 补充 mount、认证、lifespan、配置测试。

### Phase 2：Credential / Provider 验证

- 优先验证 AgentScope 官方 credential 流程。
- 如需项目全局 provider adapter，再复用 `LLM_*` 配置。
- 不把 `.secrets` 中的 key 写入文档、日志、workspace 或前端 schema default。

### Phase 3：KB 运维 tool / skill

- 将旧 KB Copilot 设计中的 catalog、diagnostic、retrieval probe 能力改造成 AgentScope tool / skill。
- Tool 调用项目 Service，Service 调用 RAG / Vector / task。
- 不恢复旧 `/v1/agentscope/kb-copilot/*` API。

### Phase 4：真实任务与权限增强

- 接入真实 KB ORM、IndexTask、Celery workflow。
- 使用 AgentScope-native permission / HITL 或项目确认流时，先完成独立设计。
- 完成审计、幂等、回滚和环境隔离说明。

## 16. 与旧设计的关系

旧 KB Copilot 设计文档已选择性合并到本文档并删除。已合并的内容：

- 项目已有 RAG / Vector / KB 诊断能力边界。
- Tool 必须经项目 Service，不直接访问持久化或外部系统。
- AgentScope permission / HITL 不是最终安全边界。
- 高风险知识库操作需要 Service 层权限、幂等、审计和环境校验。
- Mock / demo 能力不能伪装成真实 KB ORM 管理后台。

未保留的旧内容：

- 自造 `agentscope_kb` controller、schema、DTO、service 目录结构。
- `/v1/agentscope/kb-copilot/*` API contract。
- 项目自定义 SSE event 作为主协议。
- 自建 AgentScope state 存储与 pending action confirm API 作为首版主路径。
