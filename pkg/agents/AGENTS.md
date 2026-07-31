# AGENTS.md

`pkg/agents/` 提供与业务无关的结构化 Agent 执行循环和状态 contract。

- Action Maker 输出归一为 `AgentToolCall` / `AgentFinal`；Tool 显式声明名称、参数 schema 和 handler，
  runner 不解析自然语言动作协议。
- 执行循环必须有明确步数上限；工具错误可按策略记录，非法 Action Maker 输出和未知编程错误不得静默吞掉。
- runner 不直接调用真实 LLM、网络、数据库或 Redis，外部能力通过协议和 handler 注入。
- 公开 action、tool、run/step 状态、异常及枚举可能进入日志或持久化记录，字段和默认错误策略按兼容性
  contract 处理。
- 变更至少覆盖正常完成、未知工具、工具异常、非法动作和最大步数。
