# AGENTS.md

适用于 `pkg/agents/`。父级约束见 `pkg/AGENTS.md`。

## 模块职责

本目录提供通用 Agent 运行基础设施，当前核心是结构化 ReAct / Tool Calling
执行循环：

- `react.py`：结构化工具定义、Action Maker 协议、有限步执行器、运行状态记录和
  LLM payload 解析。
- `llm_action.py`：基于结构化 LLM 输出的通用 ReAct action maker 和动作 schema。
- `__init__.py`：导出外部可复用的 Agent 类型、异常和 helper。

该包只负责 Agent 编排基础能力，不绑定具体业务场景、具体 LLM Provider、
HTTP API、数据库、缓存或消息队列。

## 编码约定

- 保持零业务依赖：不得 import `internal/`，业务侧通过注入 `action_maker` 和
  `StructuredTool.handler` 接入模型、DAO、缓存或外部服务。
- Action Maker 边界使用 `AgentActionMaker.make_next_action(context)` 协议表达；
  新增动作 helper 时，输入输出必须仍归一到 `AgentToolCall` / `AgentFinal`。
- Tool 定义保持结构化：工具名、描述、参数 schema、handler 都必须显式；不要让
  runner 解析自然语言动作或依赖脆弱字符串协议。
- 执行循环必须有明确上限；任何新 runner 或循环扩展都要保留 `max_steps` 类似的
  防无限循环保护。
- 状态记录应保留动作、`action_result`、错误和耗时等调试字段；新增字段要考虑日志、
  trace、持久化和测试断言的兼容性。
- 默认不要在 runner 内吞掉未知编程错误。工具执行错误可以按配置捕获成
  `action_result`，但 Action Maker 返回非法动作应显式抛出。
- 不在本包内直接调用真实 LLM SDK、网络、数据库或 Redis；需要 provider 适配时，
  放在调用方或单独 adapter，并通过协议/函数注入。

## 兼容性要求

- `AgentToolCall`、`AgentFinal`、`StructuredTool`、`AgentRunState`、
  `AgentRunResult`、`AgentStepRecord` 和公开异常属于通用契约；改字段、改异常类型、
  改默认错误处理策略前必须全仓检索调用方。
- `parse_agent_action()` 支持的 payload 形态是 LLM 输出适配契约，变更
  `type`、`tool`、`args`、`answer`、`call_id` 语义时要同步测试和调用方说明。
- `AgentRunStatus` / `AgentStepStatus` 的枚举值可能进入日志、trace 或持久化记录；
  改名或删除按兼容性变更处理。

## 验证重点

- 修改 `pkg/agents/` 时优先运行：

  ```bash
  uv run pytest tests/agents/test_react.py -q
  uv run ruff check pkg/agents tests/agents
  uv run mypy pkg/agents
  ```

- 修改格式、docstring 或导出时，补跑：

  ```bash
  uv run ruff format --check pkg/agents tests/agents
  ```

- 新增 provider adapter、状态字段、错误策略或循环控制时，测试至少覆盖 happy path、
  未知工具、工具异常、非法 Action Maker 输出和最大步数上限。
