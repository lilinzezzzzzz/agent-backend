# Agent 实现方式与技术选型

本目录提供通用 Agent 运行基础设施。当前实现以结构化 ReAct / Tool Calling 为核心，
支持有限循环、工具调用和运行状态记录。

Agent 并不等同于 ReAct。选择实现方式时，应优先考虑任务是否确定、是否有副作用、
是否需要动态选择工具，以及业务流程是否要求强一致性。

## 常见 Agent 实现方式

| 实现方式 | 核心特点 | 适合场景 | 主要风险 |
| --- | --- | --- | --- |
| Tool Calling | LLM 根据用户输入选择一次工具，应用执行工具并返回结果 | 简单查询、单工具操作、参数提取 | 不适合依赖多轮 observation 的任务 |
| ReAct | LLM 根据当前状态动态决定调用工具或返回最终答案 | 客服、搜索、RAG、分析、动态多工具调用 | 工具选择和执行路径不完全稳定 |
| Router Agent | LLM 或分类器识别意图，再路由到专用 Agent、Service 或 Workflow | 多业务域客服、多个专业能力入口 | 路由错误会把请求交给错误模块 |
| Plan-and-Execute | 先生成执行计划，再逐步执行和校验计划 | 长任务、研究、复杂分析、跨系统任务 | 计划可能过时，执行成本和延迟较高 |
| Workflow / State Machine | 代码预定义步骤、状态和转换条件 | 支付、退款、审批、订单状态流转 | 灵活性较低，流程变更需要修改代码 |
| Deterministic Pipeline | 按固定步骤执行，不由 LLM 动态决策 | ETL、批处理、固定数据加工 | 不适合开放式用户请求 |
| Multi-Agent | 多个具备不同职责的 Agent 协作 | 复杂研究、生成与审查、跨领域任务 | 成本、延迟、调试和一致性控制复杂 |

### 推荐选择顺序

1. 任务步骤固定且可以直接编码时，优先使用普通 Service 或 Deterministic Pipeline。
2. 涉及支付、退款、库存、审批等关键状态变更时，使用 Workflow / State Machine。
3. 只需要从多个能力中选择一个入口时，使用 Router Agent。
4. 需要模型根据 observation 动态选择下一步工具时，使用 ReAct。
5. 任务需要先制定较长计划再执行时，再考虑 Plan-and-Execute。
6. 只有单个 Agent 无法合理承担职责时，才考虑 Multi-Agent。

## ReAct 的适用边界

ReAct 的基础循环如下：

```text
用户输入
→ Decision Maker 判断下一步
→ AgentToolCall：执行工具并记录 observation
→ 基于 observation 继续决策
→ AgentFinal：返回最终答案
```

适合交给 ReAct 动态选择的能力：

- 查询数据库、缓存或第三方 API
- 搜索和 RAG 知识检索
- 调用确定性的金额、时间和费用计算工具
- 提交低风险异步任务
- 根据前一步 observation 决定后续查询
- 组织最终自然语言回答

不应交给 ReAct 自由编排的能力：

- 支付、退款和资金变更
- 库存扣减、释放和跨仓调度
- 订单取消、状态机迁移和事务提交
- 权限校验、幂等控制和补偿逻辑
- 必须保证顺序、原子性或强一致性的业务流程

高风险业务可以暴露为单个稳定工具，但工具内部必须由确定性的 Service 或 Workflow
完成权限、幂等、事务、状态机和补偿。

## 生产环境的混合模式

生产级 Agent 通常不是纯 ReAct，而是由 LLM、ReAct、业务 Service 和 Workflow
共同组成：

```text
HTTP / Message 入口
→ Router 或业务 Agent
→ ReAct：理解意图、选择工具、收集信息、组织回答
→ StructuredTool
→ Service / Workflow：权限、规则、幂等、事务、状态机、外部系统编排
→ observation
→ ReAct 返回最终回答
```

### 职责划分

| 层 | 负责内容 | 不应负责 |
| --- | --- | --- |
| LLM / ReAct | 理解自然语言、选择工具、根据 observation 继续决策、组织回答 | 事务、权限、金额计算、关键状态流转 |
| StructuredTool | 定义清晰参数和返回值，将 Agent 调用适配到业务能力 | 承载复杂跨域流程 |
| Service | 业务规则、权限校验、错误转换、跨能力编排 | 让 LLM 决定事务细节 |
| Workflow / State Machine | 关键流程顺序、状态迁移、重试、补偿、幂等 | 开放式自然语言理解 |
| Infra / Provider | 数据库、缓存、消息队列、LLM 和第三方客户端 | 业务规则和 Agent prompt |

### 订单业务示例

适合由 ReAct 选择的工具：

```text
get_order_status            # 查询订单事实
get_return_policy           # 查询业务规则
search_order_knowledge      # RAG 知识检索
calculate_refund_amount     # 确定性金额计算
submit_invoice_request      # 提交异步任务，只确认 queued
```

不适合拆成多个工具让 ReAct 自由编排的流程：

```text
查询订单
→ 校验取消资格
→ 释放库存
→ 发起退款
→ 更新订单状态
```

应封装成一个稳定业务工具：

```text
submit_order_cancellation
→ OrderCancellationService / Workflow
→ accepted / rejected / processing
```

## 当前实现

`pkg.agents.ReActAgent` 提供：

- `AgentDecisionMaker`：根据当前上下文决定下一步
- `AgentToolCall`：请求调用一个结构化工具
- `AgentFinal`：返回最终答案并结束运行
- `StructuredTool`：声明工具名、描述、参数 schema 和 handler
- `AgentRunState` / `AgentStepRecord`：记录决策、observation、错误和耗时
- `max_steps`：限制最大决策步数，避免无限循环

当前实现只负责通用执行循环，不直接依赖具体 LLM Provider、数据库、消息队列或业务
Service。业务接入示例见 `internal/services/agent/order.py`。

## 生产检查清单

采用 ReAct 或混合模式前，至少确认：

- 工具参数和返回值是否结构化且经过校验
- 有副作用工具是否要求用户明确意图
- 权限校验是否在工具或 Service 内强制执行
- 写操作是否具备幂等键、事务和失败补偿
- 异步任务是否只承诺 accepted / queued，而不是已完成
- 是否配置 `max_steps`、超时和重试上限
- 是否记录 tool call、observation、错误、耗时和 trace_id
- 工具失败、未知工具和最大步数耗尽时是否有明确降级策略
- prompt 是否明确工具路由、禁止事项和可直接回答的边界
