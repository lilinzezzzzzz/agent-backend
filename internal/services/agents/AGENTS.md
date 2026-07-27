# AGENTS.md

适用于 `internal/services/agents/`。父级 Service 约束见 `internal/services/AGENTS.md`，Agent 构建约束见
`internal/agents/AGENTS.md`。

## 模块职责

本目录编排业务 Agent 路由、会话持久化、确认路径、流式事件和审计。LLM / ReAct 负责理解和工具选择，
权限、确认、幂等、事务与持久化由本层及下游确定性 Service 强制执行。

## 修改约束

- `confirmation_token` 存在时先解析服务端 pending action 并确定 route；确认执行必须绕过 LLM，不能相信客户端
  重传的动作参数。
- `confirmation_token` 与 `idempotency_key` 职责不同；不得省略用户绑定、动作绑定、锁、重复键冲突和
  结果复用。
- 支付 Agent 当前只读/纯计算，不接受确认 token 或幂等键；新增真实支付副作用必须引入确定性
  Workflow、事务、渠道回调校验和补偿。
- 会话、run、message 和 step 的写入保持用户归属校验与事务边界；避免创建孤立 run 或部分写入的历史。
- 同步和流式入口的错误码、route、run_id、事件顺序和最终结果语义保持一致；SSE 适配只放在 `stream.py`。
- 审计 payload 必须递归脱敏。当前后台写入是 best-effort，不得把它描述为强审计；升级强审计需
  outbox、重试和幂等。
- Router 只路由和转发，不复制专业 Agent 业务规则；unsupported 路径保持明确降级。

## 验证重点

- 覆盖普通问答、会话续接、确认成功/重复/冲突、unsupported route、流式异常和审计脱敏。
- 修改持久化时运行相关 Service / DAO 测试；修改事件 contract 时同步 API schema 和前端消费者测试。
