# AgentScope KB Copilot Execution Plan

本文档是 `docs/agent/agentscope_kb_copilot_design.md` 的落地执行计划，目标是把
AgentScope 2.0 知识库运维 Copilot 按项目分层逐步实现到当前仓库中。

## 1. 执行原则

- 先交付轻量 mock Copilot，再扩展 pending action，最后接入真实 KB ORM 管理后台。
- Controller 只处理 HTTP contract、认证上下文和响应组装。
- Service 是业务边界，AgentScope tool 只能调用 Service，不直接访问 DAO、Redis、Milvus、Celery 或 mock 常量。
- AgentScope permission/HITL 只作为 Agent 行为闸门，真实安全边界仍在项目 Service。
- 首版不实现真实文件上传、文档解析、Milvus upsert/delete、Celery 索引重建和生产级 RBAC。
- 对外 API 和 SSE event 只暴露项目 DTO / Schema，不暴露 AgentScope 内部对象。
- 每个阶段都要保留现有 `RagService`、`ChunkVectorRepository`、`pkg/vectors/` contract。

## 2. 交付顺序

### Phase 0: 准备与基线确认

目标：确认当前项目结构、依赖、测试入口和局部约束，避免直接按设计文档盲改。

执行项：

- 读取根目录 `AGENTS.md`，以及即将修改目录下的局部 `AGENTS.md`：
  - `internal/agents/AGENTS.md`
  - `internal/controllers/AGENTS.md`
  - `internal/services/AGENTS.md`
  - `internal/services/dto/AGENTS.md`
  - `internal/schemas/AGENTS.md`
  - `internal/cache/AGENTS.md`
  - `tests/AGENTS.md`
- 检查当前已存在的 Agent、RAG、SSE、Service、DTO、Schema、cache 和测试模式。
- 检查 `pyproject.toml` 与 `uv.lock`，确认 AgentScope 依赖是否已存在。
- 检查工作区状态，避免覆盖用户已有变更。

完成标准：

- 明确实现文件清单。
- 明确可复用的现有 API、SSE helper、依赖注入和 RAG service 入口。
- 明确新增依赖是否需要更新 lockfile。

建议验证：

```bash
git status --short
rg "BaseResponse|StreamingResponse|EventSource|SSE|RagService|ChunkVectorRepository" internal tests
uv run python -V
```

### Phase 1: 轻量 Mock Copilot

目标：实现可运行的只读 AgentScope KB Copilot demo，优先验证 AgentScope runtime 与项目分层接入。

范围：

- 新增 mock KB registry。
- 新增 KB catalog、visibility、diagnostic service。
- 新增 AgentScope builder、model adapter、tools、permissions、stream adapter。
- 新增 JSON chat 与 SSE chat API。
- 只开放只读工具：
  - `list_visible_kbs`
  - `get_kb_overview`
  - `run_retrieval_probe`
  - `diagnose_kb_quality`
- 不实现 pending action。

建议文件：

```text
internal/agents/agentscope_kb/
  __init__.py
  AGENTS.md
  README.md
  builder.py
  model.py
  permissions.py
  prompt.py
  stream.py
  tools.py

internal/services/agentscope_kb/
  __init__.py
  copilot.py

internal/services/kb/
  __init__.py
  catalog.py
  diagnostics.py
  mocks.py
  visibility.py

internal/services/dto/agentscope_kb.py
internal/schemas/agentscope_kb.py
internal/controllers/api/agentscope_kb.py

tests/api/test_agentscope_kb.py
tests/services/test_agentscope_kb.py
tests/agents/test_agentscope_kb_tools.py
```

实现要点：

- `internal/services/kb/mocks.py` 文件头部明确标注 mock 数据只用于 AgentScope demo，后续由
  `docs/rag/kb_orm_admin_design.md` 的 ORM / DAO / Service 替换。
- `KbVisibilityService` 显式解析用户可见 KB scope，不通过 `RagService.retrieve()` 反推权限。
- `KbDiagnosticService.run_retrieval_probe()` 通过 `RagService.retrieve()` 走受控检索链路。
- AgentScope tool 不直接 new repository，不直接读取 mock 常量。
- 非流式接口返回 `BaseResponse[KbCopilotRunRespSchema]`。
- 流式接口将 AgentScope event 映射为项目 SSE event：
  - `run_started`
  - `message_delta`
  - `tool_call_started`
  - `tool_call_completed`
  - `run_completed`
  - `error`

完成标准：

- `/v1/agentscope/kb-copilot/chat` 可以返回 mock catalog 或诊断结果。
- `/v1/agentscope/kb-copilot/chat/stream` 可以输出稳定 SSE event。
- 无权 KB 不会进入 vector repository 直接查询。
- mock catalog 在无数据库、Redis、Celery、Milvus 的情况下可单元测试。

建议验证：

```bash
uv run pytest tests/services/test_agentscope_kb.py tests/agents/test_agentscope_kb_tools.py
uv run pytest tests/api/test_agentscope_kb.py
uv run ruff check internal/agents/agentscope_kb internal/services/agentscope_kb internal/services/kb internal/schemas/agentscope_kb.py internal/controllers/api/agentscope_kb.py tests
```

### Phase 2: Pending Action 与确认链路

目标：为有副作用的索引重建准备动作加入项目原生 pending action，不启用 AgentScope-native paused reply。

范围：

- 新增 `KbIndexOpsService`。
- 新增 pending action cache/store。
- 新增 `prepare_rebuild_index` tool。
- 新增 confirm API。
- 新增 task status API。
- mock task 只返回 demo 状态，不真实操作 Celery 或 Milvus。

建议文件：

```text
internal/services/kb/index_ops.py
internal/cache/agentscope_kb_action.py
internal/controllers/api/agentscope_kb.py
internal/schemas/agentscope_kb.py
internal/services/dto/agentscope_kb.py
tests/services/test_agentscope_kb.py
tests/api/test_agentscope_kb.py
tests/agents/test_agentscope_kb_tools.py
```

实现要点：

- `prepare_rebuild_index` tool 正常执行，但只创建服务端可信 pending action。
- confirm API 调用 `KbIndexOpsService.confirm_rebuild_index()`，不恢复 AgentScope paused reply。
- confirm API 不接收 `reply_id` / `tool_call_id`，也不接受客户端或 LLM 构造的执行参数。
- 确认前重新校验：
  - 当前用户身份。
  - KB 可见范围。
  - pending action 归属。
  - pending action 是否过期。
  - idempotency key 是否重复或被错误复用。
  - 当前环境是否允许该操作。
- `prepare_rebuild_index` 的 SSE 映射为 `pending_action_created`。

完成标准：

- `prepare_rebuild_index` 不会直接提交真实索引任务。
- confirm 接口幂等，重复确认返回同一个 mock task。
- 不同任务复用同一个 idempotency key 会被拒绝。
- pending action 与 AgentScope session state 分开存储。

建议验证：

```bash
uv run pytest tests/services/test_agentscope_kb.py tests/api/test_agentscope_kb.py tests/agents/test_agentscope_kb_tools.py
uv run ruff check internal/cache internal/services/kb internal/services/agentscope_kb internal/agents/agentscope_kb internal/controllers/api/agentscope_kb.py tests
```

### Phase 3: 文档同步与可运行说明

目标：把 demo 能力、边界和运行方式同步到面向使用者的文档。

范围：

- 更新 `README.md`，说明 AgentScope KB Copilot demo 的启动、配置、接口和限制。
- 更新 `docs/README.md`，加入相关文档索引。
- 更新 `internal/agents/agentscope_kb/README.md`，说明 AgentScope 目录职责和 tool 边界。
- 如实现细节偏离设计文档，回写 `docs/agent/agentscope_kb_copilot_design.md`。

完成标准：

- 人类读者可以从 README 找到 demo 入口、接口、配置和限制。
- coding agent 可以从 `internal/agents/agentscope_kb/AGENTS.md` 明确该目录使用 AgentScope
  `Agent` / `Toolkit` / `ToolBase`，不受父级 `ReActAgent` / `StructuredTool` 示例约束。

建议验证：

```bash
rg "agentscope|kb-copilot|AgentScope KB" README.md docs internal/agents/agentscope_kb
```

### Phase 4: 后续接入真实 KB ORM 管理后台

目标：在 Copilot demo 稳定后，按 `docs/rag/kb_orm_admin_design.md` 将 mock registry 替换为真实
ORM / DAO / Service / API。

范围：

- 新增 `KnowledgeBase`、`KnowledgeDocument`、`KnowledgeChunk`、`KnowledgeIndexTask` ORM model。
- 新增 DAO、Service、DTO、Schema、Controller 和 DDL。
- `RagMetadataDao.get_chunk_metadata_map()` 改为批量查询真实 registry，vector hit metadata 仍作为 fallback。
- `KbCatalogService` 从 `KbMockRegistry` 切换到真实 KB Service。
- `KbIndexOpsService.confirm_rebuild_index()` 后续再提交真实 Celery task。

不在 Phase 1/2 中提前实现：

- 真实文件上传。
- 文档解析 pipeline。
- embedding refresh。
- Milvus upsert/delete workflow。
- 复杂 RBAC、分享、审批流、跨环境发布。

完成标准：

- Copilot 的 AgentScope 目录不需要随真实 registry 切换而修改。
- 只替换 Service 依赖即可从 mock registry 切到真实 registry。
- `RagService` 对外 DTO contract 不变。
- 新增表、索引和查询行为有迁移、回滚和数据安全说明。

## 3. API Contract

首版新增路由：

```text
POST /v1/agentscope/kb-copilot/chat
POST /v1/agentscope/kb-copilot/chat/stream
```

Phase 2 新增路由：

```text
POST /v1/agentscope/kb-copilot/actions/{pending_action_id}/confirm
GET  /v1/agentscope/kb-copilot/tasks/{task_id}
```

请求核心字段：

```json
{
  "session_id": "optional-session-id",
  "question": "帮我检查 KB 3 为什么召回差",
  "kb_id": 3,
  "mode": "diagnose",
  "max_steps": 6
}
```

响应要求：

- 所有非流式接口使用项目响应信封。
- SSE event payload 必须是项目 DTO / JSON object。
- `mode` 首版默认 `diagnose`。
- `kb_id` 是用户请求范围，最终 effective scope 由 Service 计算。

## 4. 安全与数据边界

- 不记录完整 API token、secret、数据库连接串、完整 chunk 原文。
- tool args 中的 token、secret、email、phone、idempotency_key 需要按项目现有脱敏规则处理。
- AgentScope raw event 不直接入库，先映射为项目审计 payload。
- pending action 中保存服务端可信参数，不信任客户端或 LLM 二次提交的执行参数。
- confirm path 绕过 LLM，不允许 AgentScope tool 自行调用 confirm。
- 高危操作首版不开放，包括删除 KB、清空 vector collection、删除孤儿 chunk、切换 embedding model、重建全部 KB。

## 5. 测试矩阵

服务层：

- `KbMockRegistry` 返回稳定 mock 数据。
- `KbCatalogService` 隐藏非当前用户可见 KB。
- `KbDiagnosticService` 调用 `RagService.retrieve()` 并正确传递 `user_id`、`kb_id`、`top_k`。
- `KbIndexOpsService` 只生成 pending action，不直接提交任务。
- confirm 校验归属、过期和 idempotency。

AgentScope adapter：

- event -> SSE event 映射稳定。
- tool result -> `tool_call_completed`。
- `prepare_rebuild_index` tool result -> `pending_action_created`。
- runtime error -> `error`。
- 同一 session 并发 stream 被锁或版本检查拒绝。

API：

- `/chat` 返回 `BaseResponse[KbCopilotRunRespSchema]`。
- `/chat/stream` 返回稳定 SSE event。
- `/v1` 默认 token 认证仍生效。
- 无权 KB 不进入 repository 直接查询。

RAG / Vector 回归：

- 不改变 `RagService` contract。
- 不改变 `ChunkVectorRepository` filter 白名单。
- 不改变 `pkg/vectors/` contract。
- mock registry 不改变 `RagMetadataDao` 行为。

## 6. 主要风险与控制

| 风险 | 控制 |
| --- | --- |
| AgentScope tool 绕过 Service 直接访问底层资源 | tool 只包装 Service 方法，测试覆盖无权 KB 不进入 repository |
| 将 AgentScope HITL 当作安全边界 | confirm path 使用项目 pending action 和 Service 校验 |
| mock 数据被误认为真实后台能力 | mock 文件头、README 和 API 文档明确标注 demo 边界 |
| SSE event 暴露 AgentScope 内部对象 | 统一 stream adapter，只输出项目 DTO / JSON |
| session state 与 pending action 互相依赖 | 二者分开存储，confirm 不依赖 AgentScope paused state |
| 新依赖导致 lockfile 或运行环境问题 | 使用 `uv add` / `uv lock` 更新，记录实际验证命令 |
| 真实 ORM 管理后台过早扩大范围 | Phase 1/2 不新增 DDL、不实现真实索引写入 |

## 7. 阶段验收顺序

1. Phase 1 service 与 tools 单测通过。
2. Phase 1 API 与 SSE 测试通过。
3. Phase 1 文档说明 demo 边界。
4. Phase 2 pending action service 单测通过。
5. Phase 2 confirm API 与 SSE `pending_action_created` 测试通过。
6. README 和 `docs/README.md` 同步。
7. 再进入真实 KB ORM 管理后台实施。

## 8. 实施时的最小命令集

依赖和锁文件：

```bash
uv add "agentscope>=2,<3"
uv lock
```

单测：

```bash
uv run pytest tests/services/test_agentscope_kb.py
uv run pytest tests/agents/test_agentscope_kb_tools.py
uv run pytest tests/api/test_agentscope_kb.py
```

Lint：

```bash
uv run ruff check internal/agents/agentscope_kb internal/services/agentscope_kb internal/services/kb internal/schemas/agentscope_kb.py internal/controllers/api/agentscope_kb.py internal/cache/agentscope_kb_action.py tests
```

按实际改动裁剪命令，不声称未运行的检查。

## 9. 退出准则

Phase 1 可认为完成，当满足：

- JSON chat 和 SSE chat 可运行。
- 只读 tools 可用。
- mock registry 边界清晰。
- 关键 service、tool、API 测试通过。
- 未引入真实持久化、真实索引写入或高危操作。

Phase 2 可认为完成，当满足：

- `prepare_rebuild_index` 只创建 pending action。
- confirm API 幂等且绕过 LLM。
- task status 返回 mock `queued` / `succeeded` / `failed` 状态。
- pending action 归属、过期、idempotency 校验测试通过。

完整产品化不属于本计划首版退出准则，需转入 `docs/rag/kb_orm_admin_design.md` 对应实施计划。
