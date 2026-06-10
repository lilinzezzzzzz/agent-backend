# Agent 实现方式与技术选型

本目录提供通用 Agent 运行基础设施。当前实现以结构化 ReAct / Tool Calling 为核心，
支持有限循环、工具调用和运行状态记录。

Agent 并不等同于 ReAct。选择实现方式时，应优先考虑任务是否确定、是否有副作用、
是否需要动态选择工具，以及业务流程是否要求强一致性。

## 常见 Agent 实现方式

| 实现方式 | 核心特点 | 适合场景 | 主要风险 |
| --- | --- | --- | --- |
| Tool Calling | LLM 根据用户输入选择一次工具，应用执行工具并返回结果 | 简单查询、单工具操作、参数提取 | 不适合依赖多轮 action_result 的任务 |
| ReAct | LLM 根据当前状态动态决定调用工具或返回最终答案 | 客服、搜索、RAG、分析、动态多工具调用 | 工具选择和执行路径不完全稳定 |
| Router Agent | LLM 或分类器识别意图，再路由到专用 Agent、Service 或 Workflow | 多业务域客服、多个专业能力入口 | 路由错误会把请求交给错误模块 |
| Plan-and-Execute | 先生成执行计划，再逐步执行和校验计划 | 长任务、研究、复杂分析、跨系统任务 | 计划可能过时，执行成本和延迟较高 |
| Workflow / State Machine | 代码预定义步骤、状态和转换条件 | 支付、退款、审批、业务状态流转 | 灵活性较低，流程变更需要修改代码 |
| Deterministic Pipeline | 按固定步骤执行，不由 LLM 动态决策 | ETL、批处理、固定数据加工 | 不适合开放式用户请求 |
| Multi-Agent | 多个具备不同职责的 Agent 协作 | 复杂研究、生成与审查、跨领域任务 | 成本、延迟、调试和一致性控制复杂 |

### 推荐选择顺序

1. 任务步骤固定且可以直接编码时，优先使用普通 Service 或 Deterministic Pipeline。
2. 涉及支付、退款、库存、审批等关键状态变更时，使用 Workflow / State Machine。
3. 只需要从多个能力中选择一个入口时，使用 Router Agent。
4. 需要模型根据 action_result 动态选择下一步工具时，使用 ReAct。
5. 任务需要先制定较长计划再执行时，再考虑 Plan-and-Execute。
6. 只有单个 Agent 无法合理承担职责时，才考虑 Multi-Agent。

## ReAct 的适用边界

ReAct 的基础循环如下：

```text
用户输入
→ Action Maker 判断下一步
→ AgentToolCall：执行工具并记录 action_result
→ 基于 action_result 继续选择动作
→ AgentFinal：返回最终答案
```

本设计中的“副作用”指请求执行后会改变系统状态、触发外部动作，或产生后续不可简单撤销
影响的操作。它和同步/异步无关；同步写数据库、异步创建任务、调用第三方、发送通知、
发起支付或退款都属于副作用。只读查询、RAG 检索、纯计算和组织回答不属于副作用。

副作用可以进一步分为两类：

| 类型 | 示例 | 处理原则 |
| --- | --- | --- |
| 可丢弃的临时副作用 | 保存 pending action、写入短期确认 token | 可以设置较短 TTL，到期失效；用于让用户确认动作摘要 |
| 真实业务副作用 | 创建业务单据、状态迁移、资金动作、发通知、创建会被 worker 消费的任务 | 必须由 Service / Workflow 执行权限校验、幂等、事务、补偿或外部系统一致性控制 |

适合交给 ReAct 动态选择的能力：

- 查询数据库、缓存或第三方 API
- 搜索和 RAG 知识检索
- 调用确定性的金额、时间和费用计算工具
- 提交低风险异步任务
- 根据前一步 action_result 决定后续查询
- 组织最终自然语言回答

不应交给 ReAct 自由编排的能力：

- 支付、退款和资金变更
- 库存扣减、释放和跨仓调度
- 业务取消、状态机迁移和事务提交
- 权限校验、幂等控制和补偿逻辑
- 必须保证顺序、原子性或强一致性的业务流程

高风险业务可以暴露为单个稳定工具，但工具内部必须由确定性的 Service 或 Workflow
完成权限、幂等、事务、状态机和补偿。

## 生产环境的混合模式

生产级 Agent 通常不是纯 ReAct，而是由 LLM、ReAct、业务 Service 和 Workflow
共同组成：

```text
HTTP / Message 入口
→ Router Service 或业务 Agent
→ ReAct：理解意图、选择工具、收集信息、组织回答
→ StructuredTool
→ Service / Workflow：权限、规则、幂等、事务、状态机、外部系统编排
→ action_result
→ ReAct 返回最终回答
```

### 职责划分

| 层 | 负责内容 | 不应负责 |
| --- | --- | --- |
| LLM / ReAct | 理解自然语言、选择工具、根据 action_result 继续选择动作、组织回答 | 事务、权限、金额计算、关键状态流转 |
| StructuredTool | 定义清晰参数和返回值，将 Agent 调用适配到业务能力 | 承载复杂跨域流程 |
| Service | 业务规则、权限校验、错误转换、跨能力编排 | 让 LLM 决定事务细节 |
| Workflow / State Machine | 关键流程顺序、状态迁移、重试、补偿、幂等 | 开放式自然语言理解 |
| Infra / Provider | 数据库、缓存、消息队列、LLM 和第三方客户端 | 业务规则和 Agent prompt |

### 副作用确认模式

涉及真实业务副作用时，推荐采用两阶段确认：

```text
用户明确提出写操作
→ ReAct 调用 prepare_* 工具
→ Service 校验权限和参数，并保存短期待确认动作
→ API 返回一次性 confirmation_token 和动作摘要
→ 客户端展示摘要，用户确认后携带 confirmation_token + idempotency_key 再次请求
→ Service 绕过 LLM，读取服务端 pending action，按 token 加锁并幂等提交
```

`confirmation_token` 是服务端保存动作的随机索引，不由 LLM 生成或修改。生产接入真实
外部系统时，仍应使用数据库唯一约束或 transactional outbox 保证任务记录和消息投递原子性。

确认请求中的两个关键字段职责不同，不能互相替代：

| 字段 | 生成方 | 作用 | 不承担 |
| --- | --- | --- | --- |
| `confirmation_token` | 服务端 | 指向服务端保存的待确认动作，绑定用户、动作类型和动作参数；确认时作为锁粒度，保证同一个动作只被执行一次 | 不处理客户端重试语义，不应由客户端或 LLM 构造 |
| `idempotency_key` | 客户端 | 标识一次确认提交请求；网络超时、客户端重试或网关重放时，返回同一执行结果 | 不描述业务动作，不提供权限，不允许绕过 `confirmation_token` 执行写操作 |

因此确认阶段必须绕过 LLM，只读取服务端保存的 pending action。确认路径先按
`confirmation_token` 加锁并检查确认结果，再检查 `idempotency_key` 对应结果：

- 相同 `confirmation_token` + 相同 `idempotency_key`：返回同一提交结果。
- 相同 `confirmation_token` + 不同 `idempotency_key`：拒绝，避免同一个待确认动作被换键重复执行。
- 相同 `idempotency_key` + 不同 `confirmation_token`：拒绝，避免客户端重试键被错误复用到另一个动作。

不适合拆成多个工具让 ReAct 自由编排的高风险流程：

```text
查询当前状态
→ 校验资格
→ 锁定资源
→ 调用外部系统
→ 更新业务状态
```

应封装成一个稳定业务工具：

```text
submit_business_action
→ BusinessService / Workflow
→ accepted / rejected / processing
```

## 当前实现

`pkg.agents.ReActAgent` 提供：

- `AgentActionMaker`：根据当前上下文生成下一步动作
- `LLMReactActionMaker`：把结构化 LLM 输出转换成 `AgentToolCall` / `AgentFinal`
- `LLMActionModel`：LLM structured output 的动作 schema
- `AgentToolCall`：请求调用一个结构化工具
- `AgentFinal`：返回最终答案并结束运行
- `StructuredTool`：声明工具名、描述、参数 schema 和 handler
- `AgentRunState` / `AgentStepRecord`：记录动作、action_result、错误和耗时
- `run()`：一次性执行完整 ReAct 循环并返回 `AgentRunResult`
- `run_events()`：以异步事件流返回 `run_started`、`step_completed` 和
  `run_completed`，适合上层 API 包装为 SSE 或消息推送
- `max_steps`：限制最大动作步数，避免无限循环

当前实现只负责通用执行循环，不直接依赖具体 LLM Provider、数据库、消息队列或业务
Service。具体业务 Agent 的 prompt、builder、tools、路由和副作用确认流程应放在
`internal/agents/<domain>/README.md`；当前订单业务示例见 `internal/agents/order/README.md`。

## 生产检查清单

采用 ReAct 或混合模式前，至少确认：

- 工具参数和返回值是否结构化且经过校验
- 有副作用工具是否要求用户明确意图
- 副作用动作是否在确认请求中绕过 LLM，并使用服务端可信确认状态
- 权限校验是否在工具或 Service 内强制执行
- 写操作是否具备幂等键、事务和失败补偿
- 异步任务是否只承诺 accepted / queued，而不是已完成
- 是否配置 `max_steps`、超时和重试上限
- 是否记录 tool call、action_result、错误、耗时和 trace_id
- 工具失败、未知工具和最大步数耗尽时是否有明确降级策略
- prompt 是否明确工具路由、禁止事项和可直接回答的边界
