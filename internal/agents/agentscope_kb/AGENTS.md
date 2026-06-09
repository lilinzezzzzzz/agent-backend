# AGENTS.md

适用于 `internal/agents/agentscope_kb/`。父级约束见 `internal/agents/AGENTS.md`。

## 当前状态

本目录对应早期“自建 AgentScope KB Copilot runtime glue”的历史路线，目前不再作为
AgentScope 2.0 主接入方式。当前主线是把官方 Agent Service 作为 FastAPI 子应用挂载到
`/v1/agentscope`；该实现尚未落地，计划中的 infra 封装位置是
`internal/infra/agentscope_service.py`。

## 修改约束

- 默认不要在本目录新增 `builder.py`、`stream.py`、自定义 chat controller 或 AgentScope event
  到项目 SSE/DTO 的旁路适配。
- 知识库运维能力如需继续建设，应优先建模为 AgentScope Agent Service 中的 Agent 配置、
  tool、skill、seed 数据或独立业务 Service，而不是恢复旧 `/v1/agentscope/kb-copilot/*` API。
- 如果用户明确要求恢复本目录实现，必须先同步更新
  `docs/agent/agentscope_service_integration.md`，说明为什么需要偏离官方 Agent Service contract。

## 验证重点

- 未来实施 AgentScope Service 接入时优先运行：

  ```bash
  uv run pytest tests/api/test_agentscope_service_mount.py -q
  uv run ruff check internal/infra/agentscope_service.py internal/app.py internal/config.py tests/api/test_agentscope_service_mount.py
  ```
