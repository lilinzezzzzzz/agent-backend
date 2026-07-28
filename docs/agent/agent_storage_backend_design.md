# Agent 存储后端技术方案

## 1. 背景

当前自研 Agent 入口包括：

- `/v1/agent/chat`
- `/v1/agent/chat/stream`
- `/v1/agent/order/support`
- `/v1/agent/order/support/stream`
- `/v1/agent/payment/support`
- `/v1/agent/payment/support/stream`

这些接口目前以单次请求为边界执行 ReAct：

```text
question + max_steps
-> AgentRouterService / OrderAgentService / PaymentAgentService
-> ReActAgent.run() / run_events()
-> AgentRunDTO
```

`ReActAgent` 只在本次运行内维护 `AgentRunState`。`LLMReactActionMaker`
只能看到当前 `question`、本轮 `previous_steps` 和可用工具，看不到跨请求会话历史。

仓库中已有 `agent_audit`，但它是脱敏、best-effort 的运行审计：

- 写入失败不影响业务响应。
- 通过进程内 background task 投递，进程退出或 shutdown 时可能丢记录。
- 保存的是脱敏后的输入、步骤、LLM 调用和元数据。
- 不适合承担用户可见历史、会话恢复、上下文装配或可靠 run 状态。

因此需要新增一套面向当前自研 Agent 的存储后端。

## 2. 设计结论

使用以下分层：

```text
PostgreSQL / 当前主数据库
  -> 权威保存 session、message、run、step、rolling summary
  -> 默认直接保存普通规模的工具参数和结构化结果

Redis
  -> 短期 pending action、confirmation lock、idempotency result
  -> session active lock / run lock
  -> 可选缓存最近上下文

Object Storage / OSS，大字段存储后续按需引入
  -> 首版只预留 agent_tool_artifact 表结构，不强制接入对象存储
  -> 真正需要大对象存储时，再接入项目已有 OSS provider、MinIO、S3 或阿里云 OSS

agent_audit
  -> 继续作为脱敏审计表，不参与会话恢复和上下文读取
```

当前项目还未上线，不需要为旧 API 做兼容。首版直接把 `session_id`
作为 Agent 请求和响应都显式承载的业务 contract；请求侧只有首次普通对话允许为空，
表示创建新会话。

首版直接落地数据库 backend，不引入临时内存存储。`AgentStorageBackend`
协议用于隔离 Service 与 DAO/表结构，保证 API、Service 和 Agent 上下文链路稳定；
后续如替换底层存储，不应再改 Controller contract。

## 3. 目标与非目标

### 3.1 目标

- 保存完整会话历史，支持用户继续对话、历史查看、debug 和回放。
- 保存每次 Agent run 的状态、步骤、工具参数和工具结果引用。
- 支持 JSON 和 SSE 两类接口共享同一套存储语义。
- 支持按 session 装配最近上下文，但不把全量历史塞进 prompt。
- 保持 Controller 薄层，存储编排放在 Service，数据库访问放在 DAO。
- 保留 Redis confirmation / idempotency 现有语义，不把短期确认状态迁移到 DB。
- 首版直接使用数据库 backend 验证接口和 Service 链路。
- 为后续 rolling summary、long-term memory、tool artifact 和向量检索预留结构。

### 3.2 非目标

- 不把 `pkg.agents.ReActAgent` 改造成依赖数据库的 runner。
- 不把 `agent_audit` 升级成会话存储。
- 不在 Controller 中直接读写数据库、Redis 或拼装上下文。
- 不在首版实现长期记忆抽取、embedding、上下文预算 packer 的完整能力。

## 4. API Contract

因为当前项目未上线，首版直接调整 contract。

### 4.1 请求字段

`AgentReqSchema` 调整为：

```text
session_id: str | None
question: str | None
max_steps: int
confirmation_token: str | None
idempotency_key: str | None
```

语义：

- 普通对话：`question` 必填；`session_id` 为空时创建新会话，非空时继续已有会话。
- 确认请求：`confirmation_token`、`idempotency_key` 和 `session_id` 必填；`question` 不参与业务参数。
- `session_id` 非空时，Service 必须校验 `user_id` 归属。
- `confirmation_token` 和 `idempotency_key` 仍必须成对出现。
- 确认执行必须只信任服务端 pending action，不信任客户端重传的业务参数。

### 4.2 响应字段

`AgentRunRespSchema` 调整为：

```text
session_id: str
run_id: str
status: str
answer: str | None
steps: list[AgentStepSchema]
confirmation: AgentActionConfirmationSchema | None
```

`AgentChatRespSchema` 保持 Router 结构，但内部 result 带 `session_id`：

```text
route: str
result: AgentRunRespSchema
```

### 4.3 会话接口

首版建议一起提供只读会话能力，否则前端只能继续会话，不能查看历史：

```text
GET    /v1/agent/sessions
GET    /v1/agent/sessions/{session_id}/messages
DELETE /v1/agent/sessions/{session_id}
```

语义：

- `GET /sessions`：分页读取当前用户会话列表。
- `GET /sessions/{session_id}/messages`：分页读取当前用户某个 session 的消息。
- `DELETE /sessions/{session_id}`：软删除或关闭会话，不物理删除历史数据。

所有接口都走 `/v1` 默认 token 认证，并按 `user_id` 做资源归属校验。

## 5. 分层架构

```text
internal/controllers/api/agent.py
  -> 只负责 schema、Depends、get_user_id() 和 response 组装

internal/services/agents/router.py
internal/services/agents/order.py
internal/services/agents/payment.py
  -> Agent 业务入口
  -> 调用 AgentConversationService 管理 session/run/message/step
  -> 调用业务 Agent builder 和 ReActAgent

internal/services/agents/conversation.py
  -> 新增：会话存储用例
  -> start_run / complete_run / fail_run / load_context / list_messages
  -> 处理事务、锁、归属校验、错误转换

internal/dao/agent_session.py
internal/dao/agent_message.py
internal/dao/agent_run.py
internal/dao/agent_run_step.py
  -> 新增：ORM 数据访问

internal/models/agent_session.py
internal/models/agent_message.py
internal/models/agent_run.py
internal/models/agent_run_step.py
  -> 新增：SQLAlchemy ORM 映射

internal/cache/agent_session.py
  -> 可选新增：active session/run lock
```

`pkg/agents` 继续保持通用 runner，不 import `internal`，也不直接访问数据库或 Redis。

## 6. 存储接口

业务 Service 不应散落调用多个 DAO。建议先定义一个窄接口：

```python
class AgentStorageBackend(Protocol):
    async def start_run(
        self,
        *,
        user_id: int,
        session_id: str | None,
        entrypoint: str,
        agent_name: str,
        question: str,
        max_steps: int,
        trace_id: str | None,
    ) -> AgentRunStartDTO: ...

    async def load_context(
        self,
        *,
        user_id: int,
        session_id: str,
        max_recent_messages: int,
        max_recent_chars: int,
    ) -> AgentConversationContextDTO: ...

    async def complete_run(
        self,
        *,
        user_id: int,
        session_id: str,
        run_id: str,
        route: str | None,
        result: AgentRunDTO,
    ) -> None: ...

    async def fail_run(
        self,
        *,
        user_id: int,
        session_id: str,
        run_id: str,
        error_code: str,
        error_message: str,
    ) -> None: ...
```

实现类放在 `internal/services/agents/conversation.py`，内部组合 DAO。

好处：

- Order / Payment / Router Service 不需要知道表结构。
- 后续可把底层从当前主数据库替换为其他 backend。
- 测试可以用 fake backend 覆盖 Agent Service 行为。
- 事务和批量写策略集中管理。

### 6.1 Database backend

首版直接实现 `DatabaseAgentStorageBackend`：

```text
internal/services/agents/conversation.py
  -> AgentConversationService
  -> DatabaseAgentStorageBackend
  -> AgentSessionDao / AgentMessageDao / AgentRunDao / AgentRunStepDao
```

database backend 规则：

- 使用当前主数据库保存 session、message、run、step。
- 在 `start_run()` 中生成真实格式的 `session_id`、`run_id`、`message_id`，不要返回硬编码 ID。
- 校验 `user_id + session_id` 归属，保持权限语义和真实 backend 一致。
- `start_run()` 负责创建或校验 session、写入 user message、创建 running run。
- `complete_run()` 负责写 assistant message、批量写 run steps、更新 run/session 状态。
- `fail_run()` 负责把 run 标记为 failed/cancelled，并记录稳定错误码和脱敏错误信息。
- 最近消息读取必须分页并稳定排序，不能无边界加载全量历史。
- 写入路径通过 DAO 完成，Controller 不直接访问数据库。
- 不在数据库事务中执行 LLM 调用、RAG 检索或外部网络请求。

本地和测试环境也默认使用 database backend。单元测试如需隔离数据库，可以在测试内
注入轻量 fake 实现覆盖 `AgentStorageBackend` 协议，但 fake 只作为测试替身，不进入
运行时配置、部署方案或产品链路。

因此首版不需要新增存储后端切换配置；`new_agent_conversation_service()`
固定组装 `DatabaseAgentStorageBackend` 和对应 DAO。

## 7. 数据模型

以下字段是落地 ORM / DDL 的建议形态。命名需结合现有 `ModelMixin`：
`id`、`creator_id`、`creator_type`、`created_at`、`updater_id`、`updater_type`、
`updated_at`、`deleted_at`
由项目基类承载。

### 7.1 agent_session

```text
agent_session
- id bigint primary key
- session_id varchar(64) not null unique
- user_id bigint not null
- entrypoint varchar(32) not null
- status varchar(32) not null
- title varchar(128) null
- rolling_summary text null
- working_state json null
- recent_token_count int not null default 0
- message_count int not null default 0
- last_message_at datetime null
- expires_at datetime null
- creator_id bigint null
- creator_type varchar(32) not null
- created_at datetime not null
- updater_id bigint null
- updater_type varchar(32) null
- updated_at datetime null
- deleted_at datetime null
```

索引：

```text
unique uq_agent_session_session_id(session_id)
index idx_agent_session_user_status_updated(user_id, status, updated_at)
index idx_agent_session_user_last_message(user_id, last_message_at)
```

设计要点：

- `session_id` 是对外 ID，不直接暴露雪花主键。
- `working_state` 保存当前任务状态，不保存无边界全文。
- `rolling_summary` 保存旧消息压缩摘要。
- `last_message_at` 用于列表排序；需要显式排序和稳定分页。

### 7.2 agent_message

```text
agent_message
- id bigint primary key
- message_id varchar(64) not null unique
- session_id varchar(64) not null
- run_id varchar(64) null
- user_id bigint not null
- role varchar(16) not null
- content text not null
- content_summary text null
- token_count int not null default 0
- metadata json null
- creator_id bigint null
- creator_type varchar(32) not null
- created_at datetime not null
- updater_id bigint null
- updater_type varchar(32) null
- updated_at datetime null
- deleted_at datetime null
```

`role` 取值：

```text
user
assistant
tool
system
```

索引：

```text
unique uq_agent_message_message_id(message_id)
index idx_agent_message_session_created(session_id, created_at, id)
index idx_agent_message_user_created(user_id, created_at, id)
index idx_agent_message_run_id(run_id)
```

设计要点：

- 用户原文和 assistant 最终回答保存在 message。
- 工具大结果不直接塞进 `content`，优先保存摘要和 artifact 引用。
- 历史读取必须分页，不做无边界全量查询。

### 7.3 agent_run

```text
agent_run
- id bigint primary key
- run_id varchar(64) not null unique
- session_id varchar(64) not null
- user_id bigint not null
- entrypoint varchar(32) not null
- agent_name varchar(64) not null
- route varchar(32) null
- status varchar(32) not null
- max_steps int not null
- trace_id varchar(128) null
- started_at datetime not null
- ended_at datetime null
- elapsed_ms double not null default 0
- error_code varchar(64) null
- error_message text null
- metadata json null
- creator_id bigint null
- creator_type varchar(32) not null
- created_at datetime not null
- updater_id bigint null
- updater_type varchar(32) null
- updated_at datetime null
- deleted_at datetime null
```

`status` 取值：

```text
running
completed
max_steps_reached
failed
cancelled
```

索引：

```text
unique uq_agent_run_run_id(run_id)
index idx_agent_run_session_created(session_id, created_at, id)
index idx_agent_run_user_created(user_id, created_at, id)
index idx_agent_run_agent_status(agent_name, status)
index idx_agent_run_trace_id(trace_id)
```

设计要点：

- `run_id` 在 `start_run()` 时生成，然后传给 `ReActAgent.run(..., run_id=run_id)`。
- JSON 和 SSE 都应先创建 `running` run，再执行 Agent。
- `agent_audit` 可继续用相同 `run_id` 做审计关联，但不反向依赖 audit。

### 7.4 agent_run_step

```text
agent_run_step
- id bigint primary key
- run_id varchar(64) not null
- session_id varchar(64) not null
- user_id bigint not null
- step_index int not null
- status varchar(32) not null
- action_type varchar(32) not null
- tool varchar(64) null
- args json null
- action_result json null
- artifact_id varchar(64) null
- error text null
- elapsed_ms double not null default 0
- creator_id bigint null
- creator_type varchar(32) not null
- created_at datetime not null
- updater_id bigint null
- updater_type varchar(32) null
- updated_at datetime null
- deleted_at datetime null
```

索引：

```text
unique uq_agent_run_step_run_index(run_id, step_index)
index idx_agent_run_step_session_created(session_id, created_at, id)
index idx_agent_run_step_user_created(user_id, created_at, id)
index idx_agent_run_step_tool(tool)
```

设计要点：

- run 完成后使用 DAO `insert_rows()` 批量写入 steps。
- 不在每个 step 事件中执行一条独立 ORM 写入，除非后续明确需要强实时 step 恢复。
- `action_result` 默认保存普通规模的结构化结果。只有超过阈值或不适合直接入库的结果才放
  artifact，prompt 中只放摘要。

### 7.5 agent_tool_artifact

首版可以延后完整 artifact 存取能力，但表结构应预留。落地规则：

- 小型工具结果直接存 `agent_run_step.action_result`。
- 大型或文件型结果先预留 `agent_tool_artifact` 表结构，不强制首版接入对象存储。
- 真正需要大对象存储时，再接入项目已有 OSS provider、MinIO、S3 或阿里云 OSS。

大多数工具调用不会产生大字段数据，可以直接把脱敏后的结构化结果写入
`agent_run_step.action_result`；只有命中阈值或文件型结果时才创建 artifact。

```text
agent_tool_artifact
- id bigint primary key
- artifact_id varchar(64) not null unique
- session_id varchar(64) not null
- run_id varchar(64) not null
- user_id bigint not null
- tool varchar(64) not null
- input_summary text null
- output_summary text null
- output_ref varchar(512) null
- output_bytes bigint not null default 0
- output_token_count int not null default 0
- status varchar(32) not null
- error_code varchar(64) null
- metadata json null
- creator_id bigint null
- creator_type varchar(32) not null
- created_at datetime not null
- updater_id bigint null
- updater_type varchar(32) null
- updated_at datetime null
- deleted_at datetime null
```

适用场景：

- RAG 检索返回大量 evidence。
- 日志查询、文件读取、报表计算等大体积工具结果。
- 后续需要按 artifact 二次读取详细结果。

不适用场景：

- 订单状态、支付方式、金额计算这类小型 JSON 结果。
- 可以安全裁剪到摘要字段内的工具结果。
- 只服务当前 run、后续无需二次读取的临时数据。

## 8. 运行链路

### 8.1 普通 JSON 请求

```text
1. Controller 读取 user_id，传入 Service。
2. Service 调用 AgentConversationService.start_run()。
   - session_id 为空则创建 session。
   - session_id 非空则校验 user_id 归属。
   - 写入 user message。
   - 创建 agent_run(status=running)。
3. Service 调用 load_context() 读取 rolling_summary + 最近消息窗口。
4. Builder 创建 ReActAgent。
5. ActionMaker 在模型消息中加入 session_context。
6. Service 调用 agent.run(user_input=question, run_id=run_id)。
7. Service 将 AgentRunResult 转成 AgentRunDTO。
8. complete_run() 批量写 assistant message、run status、steps，并更新 session。
9. record_agent_audit() 继续异步写审计。
10. Controller 返回 BaseResponse。
```

失败路径：

- 业务错误：`fail_run()` 标记失败，然后继续抛 `AppException`。
- 未知错误：`fail_run()` 标记失败，转换为 `errors.ServiceUnavailable`。
- 存储失败：不能返回成功；应转换为 `errors.ServiceUnavailable`，避免用户以为会话已保存。

### 8.2 SSE 请求

SSE 需要边发事件边执行，但仍要保证最终状态可靠。

```text
1. stream_* Service 开始时创建 session 和 run(status=running)。
2. 立即 yield run_started。
3. 每个 ReAct step 转成 step_completed 事件。
4. run_completed 前调用 complete_run()。
5. complete_run() 成功后再 yield run_completed。
6. 如果 DB 写入失败，yield error，不 yield run_completed。
```

客户端断开：

- 捕获取消或生成器退出时，尽量调用 `fail_run(status=cancelled)`。
- 如果无法完成取消标记，下一次后台清理任务可将长时间 running 的 run 标记为 failed/cancelled。

### 8.3 确认请求

confirmation 路径必须绕过 LLM：

```text
confirmation_token + idempotency_key
-> AgentConfirmationResolver.resolve()
-> Redis pending action / lock / idempotency result
-> route 对应的专业 Service confirm 方法
-> AgentConversationService.complete_run()
```

要求：

- Router 不能把 `confirmation_token` 默认等同于订单业务，必须先从服务端 pending action
  读取可信 `route`，再分发到对应专业 Agent。
- pending action 必须保存 `route`、`action`、`user_id` 和业务 payload；`route` 不信任客户端传入值。
- 不信任客户端重传的订单号、发票抬头、税号、邮箱等参数。
- `confirmation_token` 不保存明文到 message 或 run metadata；如需关联，只保存 hash 或脱敏值。
- 确认结果需要写入同一个 session，便于用户看到“已提交/queued”的上下文。

## 9. 上下文装配

首版不实现完整 200k context packer，但要建立正确边界。

`load_context()` 返回：

```text
session_id
rolling_summary
recent_messages
working_state
truncated
```

默认策略：

- 保留最近 N 条消息，N 由配置控制，例如 20。
- 同时设置最大字符数，例如 12000，避免一条超长消息撑爆 prompt。
- 工具结果默认只放摘要，不放完整 JSON。
- 超限时从旧到新裁剪，保留当前用户输入和最近 assistant 回复。

ActionMaker 输入形态建议：

```json
{
  "question": "当前用户问题",
  "session_context": {
    "rolling_summary": "...",
    "recent_messages": [
      {"role": "user", "content": "..."},
      {"role": "assistant", "content": "..."}
    ],
    "working_state": {}
  },
  "available_tools": [],
  "previous_steps": [],
  "output_contract": {}
}
```

后续完整 context packer 参考 `agent_memory_context_design.md`。

## 10. Redis 使用边界

继续保留现有 `internal/cache/agent_action.py`：

- pending action
- confirmation result
- idempotency result
- confirmation lock

新增可选 `AgentSessionCache`：

```text
agent_session:lock:{session_id}
agent_run:lock:{run_id}
agent_session:recent_context:{session_id}
```

设计要点：

- lock TTL 必须短，例如 10-30 秒，并在 finally 中释放。
- 同一 session 默认不允许并发 run，避免消息顺序错乱。
- Redis cache 只是加速，不是权威存储；miss 时从 DB 读取。
- 不把长期消息或用户可见历史只保存在 Redis。

## 11. 安全与数据治理

### 11.1 权限隔离

- 所有 session、message、run、step 查询必须带 `user_id`。
- `session_id` 不是权限凭证；只能作为资源定位符。
- 不存在或无权访问都可以返回 `errors.Forbidden` 或 `errors.NotFound`，
  具体错误语义需在 Service 内统一。

### 11.2 敏感信息

不得保存或输出：

- 密码、token、cookie、API key、私钥。
- 数据库连接串明文。
- 未脱敏的 confirmation token。

可保存但需治理：

- 用户问题原文。
- assistant 回复。
- 工具输入和工具结果。
- 邮箱、手机号、税号等 PII。

建议：

- 对 `content_summary`、`input_summary`、`output_summary` 复用审计脱敏能力。
- `metadata` 中避免保存完整请求头、Authorization 或 secret。
- 日志只打印 `session_id/run_id/user_id/agent_name/status` 等定位字段。

### 11.3 Retention

首版建议：

- 用户可见会话软删除，不物理删。
- 后台任务定期清理已软删除且超过保留期的数据。
- tool artifact 可设置更短保留期。
- audit 的保留策略与用户会话历史分开管理。

## 12. 数据库与迁移注意事项

当前未上线，因此可以直接创建目标表，不需要做向后兼容迁移。

仍需注意：

- 建表应与 SQLAlchemy model 同步。
- 所有列表查询必须分页。
- 最近消息读取需要稳定排序：`created_at, id`。
- run steps 使用批量插入，不在循环里逐条 ORM 写入。
- 不要在数据库事务中执行 LLM 调用、RAG 检索或外部网络请求。
- `running` run 清理需要后台任务或管理命令兜底。

推荐部署顺序：

```text
0. 新增 DDL 和 ORM model。
1. 新增 DAO 和 DatabaseAgentStorageBackend。
2. 调整 schema 和 controller docstring。
3. 接入 JSON Agent Service。
4. 接入 SSE Agent Service。
5. 增加 session list/messages/delete API。
6. 补充测试和 README/docs 同步。
```

## 13. 测试计划

Service 单元测试：

- storage backend 创建 session、追加消息、读取最近上下文。
- 未传 `session_id` 创建新会话。
- 传入他人 `session_id` 返回无权限。
- 普通 run 成功后写 user message、assistant message、run、steps。
- Agent 异常时 run 标记 failed。
- confirmation 请求绕过 LLM，并写入确认结果消息。
- 同一 session 并发 run 获取锁失败或串行化。

DAO / persistence 测试：

- 按 `session_id + user_id` 查询。
- 最近消息分页稳定排序。
- run step 批量写入和唯一约束。
- soft delete 后默认查询不可见。

API 测试：

- `/v1/agent/chat` 返回 `session_id`。
- 使用返回的 `session_id` 继续对话。
- `/v1/agent/chat/stream` 在完成前后事件顺序正确。
- session messages 接口分页返回当前用户数据。
- 无权访问其他用户 session 被拒绝。

建议命令：

```bash
uv run pytest tests/api/test_agent.py tests/services/test_agent_conversation.py -q
uv run ruff check internal/controllers/api/agent.py internal/services/agents internal/schemas/agent.py tests/api/test_agent.py tests/services/test_agent_conversation.py
```

## 14. 分阶段落地

### Phase 0：数据库存储基础链路

状态：已落地完成。

- 定义 `AgentStorageBackend` 协议和 DTO。
- 新增 session、message、run、step 表。
- 新增 DAO 和 `DatabaseAgentStorageBackend`。
- 接入 `AgentConversationService`。
- 调整 Agent 请求/响应 `session_id` contract。
- JSON Agent 接口完成可靠写入。

### Phase 1：SSE 存储一致性

- SSE 开始前创建 running run。
- run completed 前完成 DB 写入。
- 客户端断开时标记 cancelled。
- 增加 running run 清理任务。

### Phase 2：会话读取 API

- `GET /v1/agent/sessions`
- `GET /v1/agent/sessions/{session_id}/messages`
- `DELETE /v1/agent/sessions/{session_id}`
- 完善分页、排序、软删除。

### Phase 3：上下文摘要与 artifact

- 增加 rolling summary。
- 预留 `agent_tool_artifact` 表结构。
- 大工具结果默认以摘要和引用进入 prompt。
- 需要大对象存储时，再接入项目已有 OSS provider、MinIO、S3 或阿里云 OSS。
- 摘要和 artifact 清理通过 Celery 或后台任务执行。

### Phase 4：长期记忆和检索

- 新增 `agent_memory`。
- 支持 owner scope、memory type、source event、status、expires_at。
- 通过 `pkg/vectors` 对 memory/document chunk 做语义检索。
- 引入统一 context packer。

## 15. 与现有文档关系

- 本文档约束当前自研 ReAct Agent 的存储后端落地。
- `agent_memory_context_design.md` 约束长期记忆、200k context 和上下文编排的长期方向。
- `agentscope_service_integration.md` 约束 AgentScope 官方 Agent Service 接入，不复用本文的自研存储表。
- `pkg/agents/README.md` 继续约束 ReAct runner 和副作用确认模式。
