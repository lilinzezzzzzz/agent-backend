# Agent 受管理运行（打断与恢复）使用说明

本文记录当前已实现的行为，不是目标设计。协议、状态机和存储事实分别以
`pkg/agents/`、`internal/services/agents/conversation.py` 与 `ddl/postgresql/init.sql` 为准；
更完整的取舍与验证矩阵见
[Agent 打断与原 run 恢复技术设计](../../plans/pending/AGENT_INTERRUPT_RESUME_DESIGN.md)。

## 能力边界

- 受管理运行把执行现场（checkpoint）持久化到数据库，支持协作式打断与恢复**同一个 run**。
- 打断是安全边界上的协作式语义：不强制杀死任意工具调用；只有取消收尾预算到期时才会取消执行任务
  （见「请求取消与收尾」）。恢复也不保证复现未完成 LLM 调用的相同输出。
- 不提供进程内存快照、token 级续生成、任意业务副作用的 exactly-once、自动恢复定时任务或 SSE 事件重放。
- 现有六个聊天/支持接口保持原有请求与响应语义；它们不会因为有 `run_id` 就自动变为可恢复。

## 生命周期

1. `POST /v1/agent/runs/create` 创建会话、用户消息、`ready` 状态的 run 和初始 checkpoint，
   不调用模型。统一入口初始阶段为 `routing`，专业入口为 `before_action`。
2. `POST /v1/agent/runs/{run_id}/resume` 或 `.../resume/stream` 原子 claim 该 run，
   获得新的 fencing token 后执行；第一次执行发送 `run_started`，后续 attempt 发送 `run_resumed`。
3. 执行器在每个安全点提交 checkpoint；提交与打断检查是同一个原子控制操作。
4. `POST /v1/agent/runs/{run_id}/interrupt` 只提交控制意图；响应返回 `interrupt_requested`
   表示已受理，客户端继续用 `GET /v1/agent/runs/{run_id}` 查询。
5. 暂停以 `run_interrupted` 结束本次流，不发送 `run_completed`，也不写 assistant 消息。
6. 恢复继续执行到暂停或终态；终态提交在同一个事务中写入 final step 和唯一 assistant 消息。

状态取值与允许的迁移：

| 状态 | 含义 | 允许的后续状态 |
| --- | --- | --- |
| `ready` | 初始 checkpoint 已提交，尚未执行 | `running` |
| `running` | 某个有效 lease 持有者执行中 | `interrupt_requested`、`interrupted`、`completed`、`max_steps_reached`、`failed`、`recovery_required` |
| `interrupt_requested` | 打断请求已持久化，正在到达安全点 | `interrupted`、`failed`、`recovery_required` |
| `interrupted` | 安全 checkpoint 已提交，无活动执行者 | `running` |
| `recovery_required` | 工具结果不确定或状态无法安全重放 | 首期禁止普通恢复 |
| `completed` / `max_steps_reached` / `failed` | 终态 | 不允许恢复为运行中 |

## 请求取消与收尾

执行请求取消（客户端断连、应用关闭或慢消费）不会立刻打断正在执行的工具：

- 取消先转换为持久化打断意图，执行者不再启动新的动作或工具；
- 生产者执行与响应发送分离并带 shield，当前工具在取消收尾预算内有界地执行完并提交结果；
- 预算到期后显式取消执行任务：若取消时工具仍在飞行中，run 落为 `recovery_required`（结果不确定，
  不自动重放），否则落为 `interrupted`；状态提交与 lease 释放在同一事务完成；
- 收尾预算来自 `AGENT_CANCEL_GRACE_SECONDS`；预算内未能完成收尾时保留最近已提交 checkpoint，
  由 lease 到期后的下一次查询或恢复按「恢复判定」收敛，不会宣称暂停已成功。

## 打断状态的读取路径

打断意图只写数据库，不使用 Redis；执行者与查询四处都以数据库为唯一权威：

| 读取点 | 连接 | 说明 |
| --- | --- | --- |
| 安全点控制检查 | 主库 | `read_managed_control_state`，每个 `before_action` 前调用 |
| 原子提交内的暂停判定 | 主库（行锁事务） | 决定本次提交是推进现场还是转为 `interrupted` |
| heartbeat / control 轮询 | 主库（行锁事务） | 持续续租并回带打断状态，覆盖长工具等待 |
| 状态查询 `load_managed_run` | 主库（行锁事务） | 收敛已失去执行者的现场，并作为写后响应的事实来源 |

安全点检查必须读主库：只读副本的复制延迟会让 `interrupt_requested` 晚一个周期才可见。
查询路径同样读主库的 `load_managed_run`：它在同一个行锁事务里完成「过期执行者收敛 + 状态读取」，
因此 `GET /v1/agent/runs/{run_id}` 既能挡住丢失执行者的 run 永久停在 `running`，也不会在
`create` / `resume` 之后把已经提交的状态回退成副本上的旧值。代价是每次查询都会取一次 run 行锁，
与同一 run 的 heartbeat / 提交事务短时互斥。

Redis 只服务旧的确认动作路径（`confirmation_token`、幂等结果、执行锁）；受管理运行只在恢复前
读取它校验确认 token 是否仍有效。

### 暂停耗时的观测

每次真正暂停（`run.status` 由 `interrupt_requested` 转为 `interrupted`）都会记录一行：

```text
managed run paused by interrupt: run_id=... phase=... source=... wait_ms=...
```

- `wait_ms` = `interrupted_at - interrupt_requested_at`，即"打断受理 → 实际暂停"的耗时；
- `source` 是归类标签（`client_requested` / `client_cancelled` / `slow_client`），
  不记录客户端提供的自由文本；
- 只在暂停发生的那一刻记录：`interrupt_requested_at` 与 `interrupted_at` 会在下一次 claim
  时清空，run 一旦被恢复，这对时间戳就查不到了。

这行日志用于判断是否需要引入进程内或分布式加速通知；观测本身不会影响执行结果，
日志写入失败也不会把已提交的暂停变成执行失败。

## 恢复判定

过期 `running` / `interrupt_requested` 在查询或 resume 时先原子废止旧 token，再按 checkpoint 阶段判定：

| 最近阶段 | 恢复处理 |
| --- | --- |
| `routing` / `before_action` | 转 `interrupted`，允许重做未完成的模型调用 |
| `before_tool` | 工具尚未开始，执行已保存 action，不重新调用模型 |
| `tool_in_flight` + `replay_safe` | 允许重新调用 |
| `tool_in_flight` + `idempotent` | 按稳定调用键重新调用，由下游按该键去重或查询既有结果 |
| `tool_in_flight` + `non_replayable` | 转 `recovery_required`，不自动重复 |
| `final_ready` | 无需模型调用，继续原子完成提交 |

判定失败（无效载荷、缺失工具、步骤不连续、`definition_version` 或冻结模型配置不兼容）时拒绝恢复，
不会丢弃现场重跑，也不会静默切换 prompt、工具或供应商。

## 工具恢复策略

工具通过 `StructuredTool.replay_policy` 显式声明策略，未声明的一律按 `non_replayable` 处理：

| 工具 | 策略 |
| --- | --- |
| `get_order_status`、`get_return_policy`、`search_order_knowledge`、`calculate_refund_amount` | `replay_safe` |
| `get_supported_payment_methods`、`search_payment_knowledge`、`calculate_payment_total` | `replay_safe` |
| `prepare_invoice_request` | `non_replayable`（会写入随机 Redis pending token） |

`idempotent` 工具在重放时会额外收到参数 `_agent_tool_call_id`，其值为服务端按
`run_id + step_index` 生成的稳定调用键；工具必须据此去重或查询既有结果，否则应声明 `non_replayable`。

## 接口

| 方法和路径 | 说明 |
| --- | --- |
| `POST /v1/agent/runs/create` | `entrypoint`（`chat` / `order_support` / `payment_support`）、`question`、可选 `session_id`、`max_steps`、必填 `request_key`；不接受 `confirmation_token` |
| `GET /v1/agent/runs/{run_id}` | 状态、阶段、checkpoint 版本、attempt、完成步骤数、可恢复性、停止原因、可用 final answer；不暴露原始 checkpoint |
| `POST /v1/agent/runs/{run_id}/interrupt` | 可选有界 `reason`（≤200） |
| `POST /v1/agent/runs/{run_id}/resume` | 必填 `request_key`；不接受 `question`、`max_steps`、工具参数或替换上下文 |
| `POST /v1/agent/runs/{run_id}/resume/stream` | 同上，返回 SSE |

`request_key` 长度 8–128。同键重试返回原 attempt 的当前状态或已保存结果，不创建第二个执行者；
不同 key 对运行中 run 返回状态冲突（错误码 `40902`）。恢复不接受新的 key 之前的旧流。

SSE 事件：`route`、`run_started`、`run_resumed`、`step_completed`、`run_interrupted`、`run_status`、
`run_completed`、`error`。所有运行事件包含 `run_id`、`session_id`、`attempt_no`、`checkpoint_revision`。
等待工具期间发送 SSE 注释 `: heartbeat`。不支持 `Last-Event-ID` 或历史事件回放：重连先查询状态，
再用原 `request_key` 判定是否已有执行结果；需要新的 attempt 时必须使用新的 key。

## 配置

以下 key 在 `internal/config.py` 声明，并同步到 `configs/.env.*`：

| key | 默认值 | 说明 |
| --- | --- | --- |
| `AGENT_LEASE_SECONDS` | 30 | 执行者 lease 时长 |
| `AGENT_CONTROL_POLL_SECONDS` | 5 | heartbeat / control 轮询周期；必须小于 lease |
| `AGENT_CANCEL_GRACE_SECONDS` | 70 | 取消收尾预算；必须覆盖 `AGENT_LLM_TIMEOUT_SECONDS` 与提交预算，上限 300 |
| `AGENT_STREAM_BUFFER_SIZE` | 16 | 执行事件到响应发送之间的有界通道容量 |
| `AGENT_CHECKPOINT_MAX_BYTES` | 1048576 | 单个 checkpoint JSON 上限；超出时明确失败而不是截断后继续推理 |
| `AGENT_LLM_TIMEOUT_SECONDS` | 60 | Agent LLM 调用超时 |

## 已知未验证条件

- 并发 claim / fence、行锁与事务语义只在 SQLite 上做了逻辑验证；PostgreSQL 上的多实例并发
  需要在隔离测试库中验证。
- 真实客户端断连、慢消费、应用 shutdown 预算与 SDK 取消行为需要在部署环境确认。
- 工具实际超时、数据库性能与 checkpoint 体积分布尚无生产数据。
