# AGENTS.md

适用于 `internal/agents/agentscope_kb/`。父级约束见 `internal/agents/AGENTS.md`；
本文件只声明 AgentScope 2.0 demo 的局部例外。

## 模块职责

本目录承载 AgentScope 2.0 知识库运维 Copilot 的框架适配层：

- `builder.py`：组装 AgentScope `Agent`、`Toolkit`、model adapter 和 state。
- `tools.py`：把项目 Service 方法包装为 AgentScope custom tool。
- `permissions.py`：定义 AgentScope permission allow / deny 策略。
- `stream.py`：把 AgentScope event 映射为项目 SSE/DTO 事件。
- `prompt.py`：维护 Copilot system prompt 和工具使用边界。

业务用例入口仍放在 `internal/services/agentscope_kb/`，KB mock / diagnostic / index
能力放在 `internal/services/kb/`。HTTP contract 仍放在 `internal/controllers/` 和
`internal/schemas/`。

## AgentScope 例外

- 本子目录不使用父级 `ReActAgent` / `LLMReactActionMaker` / `pkg.agents.StructuredTool`
  作为运行时和工具协议。
- Builder 可以组装 AgentScope `Agent`、`Toolkit`、`ToolBase` / function tool 和
  AgentScope model adapter。
- Tool 定义可以使用 AgentScope 的 tool schema 和 permission 接口。
- 对外 API、Service DTO、错误码和审计仍必须映射回项目自己的类型，不暴露 AgentScope 内部对象。

## 分层约束

- 不在本目录实现 Controller、API schema、Service DTO、DAO、ORM、Redis cache 或 Celery task。
- 不在 AgentScope tool 中直接访问数据库、Redis、Milvus、Celery 或 mock 常量；工具需要业务能力时，
  只能调用注入的 Service。
- 业务权限、资源归属、pending action、幂等和真实副作用提交必须由 Service / Workflow 强制执行。
- 首版采用项目原生 pending action：`prepare_*` tool 只生成服务端 pending action，
  confirm API 绕过 LLM，不恢复 AgentScope paused reply。
- 如果未来启用 AgentScope-native HITL，必须同步设计 `reply_id`、`tool_call_id`、
  `UserConfirmResultEvent`、AgentState 恢复和并发状态保护。

## 验证重点

- 修改本目录时优先运行：

  ```bash
  uv run pytest tests/agents/test_agentscope_kb_tools.py tests/services/test_agentscope_kb.py -q
  uv run ruff check internal/agents/agentscope_kb tests/agents/test_agentscope_kb_tools.py
  ```

- 修改 event mapping、pending action 或 state 持久化时，补跑对应 API/SSE 测试。
