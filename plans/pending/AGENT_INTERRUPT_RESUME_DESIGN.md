# Agent 打断与原 run 恢复技术设计

状态：待实施。本轮仅落地技术设计，不表示接口、存储或测试已经实现。

## 1. 目标与范围

为当前结构化 ReAct Agent 增加可复用的“请求打断 → 持久化执行现场 → 恢复同一个 run”能力。
订单、支付和统一路由共用执行协议、状态机与持久化编排，业务 Builder 继续负责 prompt 和工具装配。

首期采用安全边界上的协作式打断，运行与执行请求绑定，不引入 Celery 执行队列。
打断不等于强制杀死任意工具调用；恢复也不保证复现未完成 LLM 调用的相同输出。
不做进程内存快照、token 级续生成、任意业务副作用的 exactly-once、自动恢复定时任务或 SSE 事件重放。

验收示例：同一 run 已完成步骤 0，步骤 1 的动作已持久化但工具尚未调用；用户打断后状态为
`interrupted`。恢复时不重新路由、不重建历史、不重做步骤 0，直接执行步骤 1，最终只产生一条 assistant 消息。

## 2. 当前实现与差距

以下是代码现状；后续章节均为目标设计。

| 现状 | 实现证据 | 对恢复的影响 |
| --- | --- | --- |
| 执行器每次创建空状态，从步骤 0 开始 | [ReActAgent.run_events](../../pkg/agents/react.py) | 缺少可加载的执行现场和恢复入口 |
| 非流式 Service 创建 run、加载历史，结束后保存所有步骤 | [订单 Service](../../internal/services/agents/order.py)、[会话 Service](../../internal/services/agents/conversation.py) | 历史消息不是 checkpoint，运行中缺少持久化进度 |
| 订单、支付流式路径未接入会话存储 | [支付 Service](../../internal/services/agents/payment.py) | SSE 断开后没有恢复基础 |
| 流包装器传播取消，单 chunk 等待上限为 70 秒 | [Controller](../../internal/controllers/api/agent.py)、[stream 工具](../../internal/utils/stream.py) | 取消不负责保存现场 |
| 开票确认独立执行，绕过 LLM | [OrderService](../../internal/services/order.py) | 不能作为 ReAct 恢复实现 |
| run/step 已有稳定 ID 和步骤唯一约束 | [模型](../../internal/models/agent_conversation.py) | 可以复用现有实体，避免建立第二套运行记录 |

本设计不把已完成的历史方案文档改写为当前实现；实施进度由本文末尾 checklist 管理。

## 3. 核心决策与分层

### 3.1 可复用执行协议

继续扩展 `pkg/agents`，不引入另一套业务 Agent 框架，也不依赖 `internal`。

| 层 | 目标职责 |
| --- | --- |
| `pkg/agents/state.py`（新增） | 运行快照、阶段、版本化 JSON 编解码；拒绝不可序列化状态 |
| `pkg/agents/runtime.py`（新增） | 定义 `AgentRunRuntime` 协议：读取控制状态、提交 checkpoint、检查执行权限；无数据库实现 |
| `pkg/agents/react.py` | 从新状态或已校验快照执行；在动作、工具和终态边界调用 runtime；保留原有无持久化调用方式 |
| `internal/services/agents/execution.py`（新增） | 用户授权、claim/lease、恢复校验、Builder 选择、请求生命周期和 DTO 组装 |
| 现有 `conversation.py`、DAO、Model | 实现协议所需事务、步骤保存及最终消息提交；拓展现有存储后端 |
| 现有订单/支付 Builder | 注入冻结上下文、执行配置和工具；工具的恢复策略必须显式声明 |
| Controller / `stream.py` | 仅做输入校验、响应与事件转换；不访问 session、不决定恢复规则 |

概念调用方式：`run_events(user_input=..., run_id=..., runtime=...)` 与
`resume_events(checkpoint=..., runtime=...)`。恢复入口不接受调用方替换原始问题或步骤。
协议只暴露执行需要的操作，不把数据库 session、HTTP、用户认证或订单概念传入通用执行器。

没有 runtime 的既有 `run()` / `run_events()` 保持当前行为；持久化模式必须等待存储确认后才能继续下一动作。
应用端对订单和支付共享一份编排实现，不分别复制 checkpoint/lease 逻辑。

### 3.2 新入口与兼容策略

首期新增受管理 run API，先创建 run 再执行，保证客户端在模型调用前已经拿到可打断的 `run_id`。
沿用现有 `/v1` token 认证和 `BaseResponse[T]`，DTO 放在 `internal/schemas/agent.py`。

现有六个聊天/支持接口保持现有请求及响应语义；它们不会因为有 `run_id` 就自动变为可恢复。
新增 API 复用同一订单/支付业务 Builder 和 Service 能力。前端需要切换到新生命周期才能使用本能力。
不将旧接口静默改成后台任务，不扩大旧状态枚举消费者的行为范围。

后续如需旧入口也受管理，应单独评估消费者兼容性，将其适配到同一 execution Service；不另写执行循环。

## 4. 状态机与执行现场

### 4.1 持久化运行状态

| 状态 | 含义 | 允许的后续状态 |
| --- | --- | --- |
| `ready` | 初始 checkpoint 已提交，尚未执行 | `running` |
| `running` | 某个有效 lease 持有者执行中 | `interrupt_requested`、`interrupted`、`completed`、`max_steps_reached`、`failed`、`recovery_required` |
| `interrupt_requested` | 打断请求已持久化，正在到达安全点 | `interrupted`、`failed`、`recovery_required` |
| `interrupted` | 安全 checkpoint 已提交，无活动执行者 | `running` |
| `recovery_required` | 工具结果不确定或状态无法安全重放 | 首期禁止普通恢复，保留诊断信息 |
| `completed` / `max_steps_reached` / `failed` | 终态 | 不允许恢复为运行中 |

终态提交与 interrupt 请求在同一 run 行上串行化：谁先提交谁生效。若 interrupt 先提交，执行者保存当前结果后
转为 `interrupted`，即使已经拿到 final，也在恢复时提交该 final；若终态先提交，interrupt 返回状态冲突。
恢复不重置 `max_steps`；上限耗尽不能通过反复 resume 绕过。

`interrupted` 不是正常完成，不新增 assistant 消息，不设置最终 `ended_at`。
`elapsed_ms` 累加各次实际执行时间，不包含暂停时长；首次 `started_at` 保留，各 attempt 单独记录时间及 trace。

### 4.2 Checkpoint contract

checkpoint 是恢复的唯一执行状态来源；session 历史、审计记录和客户端回传值不能替代它。

| 字段 | 含义 |
| --- | --- |
| `schema_version` | JSON 格式版本，首期为 1 |
| `revision` | 每次持久化递增的乐观并发版本 |
| `run_id` | 与数据库 run 绑定，恢复不变 |
| `phase` | `routing`、`before_action`、`before_tool`、`tool_in_flight`、`final_ready` |
| `next_step_index`、`max_steps` | 下一个未提交步骤及整个 run 的总上限 |
| `user_input`、`session_context` | 创建时冻结的输入及有界会话上下文 |
| `route`、`agent_name` | 服务端选择的业务域；路由成功后冻结 |
| `definition_version` | Builder、prompt、工具 schema/语义及序列化规则的兼容版本 |
| `model_config` | 模型标识与影响行为的非敏感参数；不含凭据 |
| `steps` | 已完成步骤的有界快照，包含 action、result/error、耗时 |
| `pending_action` | 已持久化、尚未完成的结构化 action；含稳定的工具调用标识 |
| `final_answer` | final 已生成时的待提交回答 |

工具调用标识由服务端根据 `run_id + step_index` 稳定生成，不依赖 LLM 返回的随机 `call_id`。
无效 JSON、缺失工具、步骤不连续、版本未知或已提交步骤与快照不一致时拒绝恢复，不能自动丢弃状态重跑。

`steps` 快照用于执行，`agent_run_step` 用于查询；二者在同一事务提交，快照是执行权威，步骤表不得独立修订。
冻结输入仅包含实际使用的有界上下文，不复制完整会话。恢复重新校验用户及工具资源权限，不能因快照而绕过权限变化。
不持久化 Python 对象、协程、连接或 pickle。超过大小上限的结果不能静默截断后继续推理，应结束为明确失败。

### 4.3 正常执行顺序

1. 创建 run 和用户消息，在同一事务保存初始 checkpoint。统一入口初始 phase 为 `routing`；专业入口为 `before_action`。
2. 执行请求原子 claim run，获得新的 fencing token；启动有界 heartbeat/control 任务。
3. 统一入口执行路由，持久化 route 及选定的执行配置；恢复已完成路由的 run 不再调用 Router。
4. `before_action`：检查打断及 lease，调用 LLM；校验 action 后先保存，再允许后续执行。
5. Tool action 写为 `before_tool`；再次检查打断，持久化 `tool_in_flight` 标记，再调用工具。
6. 工具返回后，以一个事务写入 step、更新快照与 index；若有打断请求则同时转为 `interrupted` 并释放 lease。
7. Final action 保存为 `final_ready`；提交终态、final step 和唯一 assistant 消息使用同一事务。
8. 只有已提交的状态才能产生对应 SSE 事件；事件丢失不回滚已完成步骤，客户端以查询接口为准。

打断检查与 checkpoint 写入必须是 runtime 的原子控制操作；不能先检查标志再无条件覆盖 run.status。
LLM 与工具网络调用不放在数据库事务里，不能长时间持有行锁。

## 5. 打断、请求取消与崩溃恢复

### 5.1 主动打断

interrupt 接口只提交控制意图，不承诺响应返回时已暂停。跨实例以数据库为权威，执行者通过 heartbeat/control
轮询及每个安全点读取状态；进程内通知只能作为加速方式。

LLM 调用可通过本地 cancel scope 尝试取消；取消未完成的生成会回到前一个 checkpoint，恢复时重新调用模型，
不保证输出相同，也不保证供应商停止计费。LLM 已生成且持久化的 action 必须复用。

工具默认不可被主动取消：等待其有界执行结束并保存结果，然后暂停。工具需要显式声明
`replay_safe`（纯计算或可重复读取）、`idempotent`（下游支持稳定调用键及结果查询）、`non_replayable`。
未声明的工具按 `non_replayable` 处理；不因函数名含 query/get 就认定安全。

### 5.2 SSE 断连、超时和应用关闭

新受管理路径将执行请求取消转换成打断意图，不继续启动下一步骤；旧路径保持现状。
管理执行 scope 与响应发送 scope 必须分离但共同归属本次请求，避免 HTTP 取消直接破坏 checkpoint 提交。
工具等待和持久化收尾采用带期限的 shield；绝不使用无限 shield，也不创建无归属的后台 task。
实现时需验证 Starlette/AnyIO 的实际取消行为，不能只依赖 generator 的隐式销毁。

新流式响应在等待工具期间发送 heartbeat（SSE 注释），与执行事件使用有界通道；慢客户端/发送超时触发打断，
不会无限积压事件。通道关闭后执行者仅做当前安全收尾，不等待向已断开的客户端发送 `run_interrupted`。
旧的 70 秒 chunk 包装策略不能原样作为整个受管理工具执行的取消策略。

若工具超时、应用关闭收尾到期或数据库不可用，无法保证完整保存正在执行的工具结果：保留最近已提交 checkpoint，
禁止声称 `interrupted` 已成功。已知不安全的未决工具进入 `recovery_required`；数据库不可用时由下次查询/恢复完成判定。

### 5.3 lease 失效与恢复判定

lease 使用数据库 UTC 时间判断，heartbeat 续租必须匹配 fencing token 且 lease 尚未失效。
所有步骤提交、终态提交、控制状态变更都校验 token/revision；旧执行者失去租约后必须停止启动新动作。
数据库 fence 只能阻止旧执行者提交，不能撤销已经发出的外部请求。

查询或 resume 发现过期 `running` / `interrupt_requested` 时，先原子废止旧 token，再依据 checkpoint 判定：

| 最近阶段 | 恢复处理 |
| --- | --- |
| `routing` / `before_action` | 转 `interrupted`，允许重做未完成模型调用 |
| `before_tool` | 工具尚未开始，允许执行已保存 action |
| `tool_in_flight` + `replay_safe` | 允许重新调用，接受读取结果可能变化 |
| `tool_in_flight` + `idempotent` | 先按稳定调用键查询结果；下游无法消除不确定性则拒绝继续 |
| `tool_in_flight` + `non_replayable` | 转 `recovery_required`，不自动重复 |
| `final_ready` | 无需模型调用，继续原子完成提交 |

首期不新增人工覆写 checkpoint 的公开接口；`recovery_required` 保留为明确的安全停止状态。
仅保存最后一个一致现场，不承诺零数据丢失或外部调用 exactly-once。

## 6. 存储、并发与幂等

在现有 `agent_run` 增加 nullable 的 `execution_version`、`checkpoint_revision`、`lease_token`、
`lease_expires_at`、`interrupt_requested_at`、`interrupted_at`、`interrupt_reason`；旧 run 的 `execution_version`
为空，不允许恢复。新状态仍由 run.status 持有，避免同时维护两套状态机。

新增 `agent_run_checkpoint`：每个 run 一行，`run_id` 唯一、`user_id`、`schema_version`、`revision`、
JSON payload、审计时间。与 run 使用逻辑引用，不增加物理外键。
新增 `agent_run_attempt`：`run_id`、递增 `attempt_no`、客户端 `request_key`、请求摘要、结果状态、trace、时间；
唯一约束为 `(run_id, attempt_no)` 和 `(run_id, request_key)`。不向客户端返回 lease token。
创建请求通过 `(user_id, create_request_key)` 唯一约束去重，并保存创建输入摘要；同键不同输入返回冲突。

claim/resume 在事务中锁定归属正确的 run，验证状态及版本，插入 attempt，递增 lease token 后提交。
同一 request_key 重试返回原 attempt 的当前状态或已保存结果，不创建新执行者；不同 key 对运行中 run 返回冲突。
重复 interrupt 在 `interrupt_requested` / `interrupted` 下返回当前状态；其他不允许状态返回冲突。

terminal 提交在 run 行锁内检查终态和 final message 是否已创建；对受管理 run 的 assistant 消息使用稳定 message_id，
复用现有唯一约束兜底。`complete_run` 必须改为复用已提交 steps，不能重复插入全部步骤。
暂停、恢复不新增用户消息；只有创建 run 和最终完成分别写一次用户/assistant 消息。

所有查询和写入都带用户归属，数据库引用由 Service 在事务内验证。会话删除/过期必须阻止活动 run 或走同一打断收尾；
未结束 run 的 checkpoint 不允许被普通历史清理删除。首期不新增自动删除任务，保留数据遵循现有会话治理边界。

索引优先复用 run/step 的唯一索引；checkpoint 主键查询和 attempt 去重增加上述唯一索引。
首期没有全库恢复扫描，不预先为状态/lease 增加未使用索引。

## 7. API 与事件 contract

以下路径为拟新增，均在 `/v1/agent` 下，需要当前用户 token。

| 方法和路径 | 请求 | 响应与语义 |
| --- | --- | --- |
| `POST /runs/create` | `entrypoint: chat/order_support/payment_support`、`question`、可选 `session_id`、`max_steps`、必填 `request_key` | HTTP 200 信封，返回 ready 的 run/session ID；只创建和冻结上下文，不调用模型 |
| `GET /runs/{run_id}` | path ID | 返回 status、phase、revision、attempt、完成步骤数、可恢复性、停止原因及可用 final result；不暴露原始 checkpoint |
| `POST /runs/{run_id}/interrupt` | 可选有界 `reason` | HTTP 200 信封，返回实际状态；`interrupt_requested` 表示已受理，客户端继续查询 |
| `POST /runs/{run_id}/resume` | 必填 `request_key` | 从 ready/interrupted 执行到暂停或终态，HTTP 200 信封包含 run/status/result；重复请求仅返回原 attempt 状态或结果 |
| `POST /runs/{run_id}/resume/stream` | 同上 | 执行并返回 SSE；重复请求输出当前状态或已保存终态后关闭，不接管或重放原流 |

`request_key` 长度 8–128，`reason` 上限 200；question/max_steps/session_id 沿用现有字段约束。
create 不接受 confirmation_token；resume 不接受 question、max_steps、工具参数或替换上下文。
既有确认请求仍走现有确认接口，恢复不成为绕过用户确认的入口。

使用现有 BadRequest、NotFound、Forbidden、ServiceUnavailable 语义；新增独立的 Agent 状态冲突、
版本不兼容、恢复不安全错误标识，实施时在错误码注册表分配未占用编号，HTTP 冲突统一映射 409。
不借用 Celery 专用 TaskStateConflict。不存在与其他用户的 run 对外统一 NotFound，防止枚举。

新 SSE 保留 `route`、`run_started`、`step_completed`、`run_completed`、`error`，增加
`run_resumed`、`run_interrupted`、`run_status`。run 创建后的第一次执行发送 run_started，后续发送 run_resumed；
所有运行事件含 run_id、session_id、attempt_no、checkpoint_revision。暂停以 run_interrupted 结束本次流，
不能发送 run_completed。SSE error 不改变已提交状态，客户端查询确认。

不支持 Last-Event-ID 或历史事件回放；重连先查询，再用原 request_key 判定是否已有执行结果。
如需在新 attempt 恢复，必须使用新 key。GET 返回已提交进度，不能用于直接重建遗漏的全部流事件。

## 8. 工具、配置版本与安全边界

订单查询、知识检索、金额计算等工具在确认业务语义后显式标记 replay_safe；每次恢复仍重新执行必要权限校验。
`prepare_invoice_request` 会创建随机 Redis pending token，不能直接视为纯读取或可靠幂等：首期按 non_replayable。
工具正常返回并保存后可继续；若崩溃在 token 写入与结果落库之间，进入 recovery_required，不自动制造第二个 token。

checkpoint 中已保存的确认 token 可能在暂停期间过期。恢复前校验此类临时依赖，过期则返回恢复不安全并停止，
不能延长授权、自动重新签发或将旧 token 当作可执行凭据。用户可发起新的普通业务请求。
真实开票确认仍走确定性 Service；本设计不修改其幂等或外部副作用 contract。

按显式 `definition_version` 注册 Builder；恢复只允许加载兼容版本，缺失时拒绝恢复，不静默切换新 prompt/工具。
冻结模型配置不包含凭据；当前配置若无法解析对应模型则失败，不暗中切换供应商。
checkpoint 可能含问题、工具结果与短期 token，按业务数据限制数据库访问；禁止写入普通日志、trace 或审计全文。
公开 GET 仅返回裁剪后的运行状态及本用户可见结果。

拟新增非敏感配置统一在 `internal/config.py` 声明，并同步 `configs/.env.*`；不增加 shell fallback。
建议初始值仅为待验证默认：lease 30 秒、heartbeat/control poll 5 秒、LLM/工具超时各 60 秒、
取消收尾上限 70 秒、checkpoint JSON 最大 1 MiB。校验 heartbeat < lease，收尾预算覆盖当前工具的剩余超时及提交预算。
持续 heartbeat 覆盖有界工具等待，不能仅依赖步骤边界续租。网关/应用关闭预算是否足够须在部署环境确认；
不满足时进入崩溃恢复语义，不宣称优雅暂停成功。首期不对失败调用做自动无限重试。

## 9. 实施顺序与验收

以下全部为待办，测试名称是拟新增用例，不表示已经存在或通过。

- [ ] 定义运行状态、checkpoint codec、工具恢复策略和 runtime 协议；旧的无 runtime runner 测试继续通过。
- [ ] 为 ReAct 增加安全点与 resume；用内存 runtime 验证步骤 0 不重做、pending action 不重生成、总步数不重置。
- [ ] 扩展现有 Model/DAO/存储后端及 PostgreSQL 基线 DDL；实现原子 step/checkpoint/terminal 写入。
- [ ] 实现 lease、fence 和 request_key；并发 resume 仅一个获执行权，旧 token 不能提交结果。
- [ ] 实现共享 execution Service 和三种 entrypoint，冻结路由/上下文；同时覆盖订单与支付，不复制循环。
- [ ] 新增 create/get/interrupt/resume/resume-stream 接口、schema 与 SSE 适配，保持旧接口 contract。
- [ ] 完成取消、慢客户端、工具超时、shutdown 和异常恢复测试；确认不可安全重放时拒绝恢复。
- [ ] 更新配置与业务使用文档，记录真实验证命令及结果后再将计划迁往 completed。

关键验证矩阵：

| 场景 | 必须观察到的结果 |
| --- | --- |
| LLM 前/中/后打断 | 已保存 action 不重生成，未完成调用允许重试 |
| 工具前/中/后打断 | 工具前不调用，工具中等待有界结果，已提交工具不重复 |
| final 与 interrupt 并发 | 遵循事务提交顺序，无暂停后偷偷完成或重复消息 |
| 两个实例同时 resume | 一个执行者；同键幂等，不同键冲突 |
| step 写入或 checkpoint 写入失败 | 原子回滚，不出现步骤与现场分叉 |
| 进程退出、lease 失效、旧进程恢复 | 根据 phase/工具策略判断；旧 token 的所有提交失败 |
| 工具外部成功但本地未提交 | 可重放工具允许重试，非幂等工具进入 recovery_required |
| SSE 断连、慢消费、取消收尾失败 | 没有无界 task/队列；GET 能区分暂停、未决和终态 |
| 模型/工具版本改变、token 过期 | 明确拒绝，不自动改配置或扩大授权 |
| 越权 run、篡改状态、同 key 不同载荷 | 拒绝；不暴露 checkpoint 或其他用户数据 |
| 已有 JSON/SSE/确认请求 | 原有结果字段、事件序列和副作用确认规则保持兼容 |

先运行 `tests/agents` 的纯执行器测试及新的 Service/API fake 测试，再运行存储集成测试。
并发 claim/fence/事务需在明确的隔离 PostgreSQL 测试库验证，标记 integration；SQLite 或 mock 不能证明 PostgreSQL 并发正确。
现有 `tests/api/test_agent.py`、`tests/services/test_agent_conversation.py`、`tests/services/test_agent_confirmation.py`
作为回归范围。运行命令使用项目 Python 3.14 / uv / pytest 配置；精确用例路径随实现补充。

## 10. 交付、回滚与未验证条件

仓库当前以全新建库为基线，实施修改 `ddl/postgresql/init.sql`，不默认新增已有环境迁移。
若上线对象已有数据，另行补充 additive migration、表规模、锁影响、混合版本与回滚演练；本设计没有这类环境证据。
旧 run 不回填虚构 checkpoint，不允许从审计反推执行现场。

启用新接口前先完成 schema 和版本兼容验证。回滚时停止创建与 claim 新受管理 run，让当前执行者有界收尾；
保留 checkpoint/attempt 数据。旧版本服务不应尝试恢复新 run；恢复能力需由支持对应 definition/schema version 的版本提供。
不通过删除表或改回状态字段完成回滚。

本轮仅进行了代码现状核对及文档检查，没有运行模型、数据库、断连或多实例实验。
工具实际超时、部署 shutdown 预算、数据库性能以及 SDK 取消行为均需实施阶段按上述矩阵验证。
