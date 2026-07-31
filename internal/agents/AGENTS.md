# AGENTS.md

`internal/agents/` 只实现业务 Agent 的 prompt、builder、router 和 `StructuredTool` 适配；业务用例入口在
`internal/services/agents/`，通用执行循环在 `pkg/agents/`。

- Builder 只组装已注入的 action maker、工具和步数上限，不创建连接或全局业务状态。
- Prompt 可以约束工具路由和输出格式，但不能成为权限、幂等、事务、金额或状态机规则的唯一执行者。
- Tool 使用结构化名称、参数 schema 和 handler；handler 只做轻量适配，业务校验和副作用提交下沉到
  Service / Workflow。
- Router 只决定业务域，不直接回答专业问题；无法可靠路由时走明确的 unsupported 降级。
- ReAct 只能准备可丢弃的 pending action。真实业务副作用必须在确认路径中绕过 LLM，并使用服务端可信状态、
  用户绑定、幂等和事务保护。
- prompt、工具参数、route 或副作用边界变化时，同步对应 Service、API contract、测试和领域 README。
