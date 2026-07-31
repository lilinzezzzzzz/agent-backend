# AGENTS.md

`internal/services/agents/` 负责业务 Agent 路由、会话持久化、确认执行、流式事件和审计。

- 存在 `confirmation_token` 时，从服务端 pending action 恢复 route 和动作参数；确认执行绕过 LLM，不信任
  客户端重传的动作内容。
- `confirmation_token` 和 `idempotency_key` 分别承担授权动作绑定和重复提交控制；保留用户绑定、执行锁、
  冲突检测及结果复用。
- 会话、run、message 和 step 写入保持用户归属和事务边界，避免孤立 run 或部分历史。
- 同步与流式入口保持错误码、route、run_id、事件顺序和最终结果一致，SSE 适配集中在 `stream.py`。
- 审计 payload 递归脱敏；当前后台审计是 best-effort，不能描述为强审计。升级时需要 outbox、重试和幂等。
- 支付 Agent 继续保持只读；新增真实支付副作用必须先引入确定性 Workflow 和补偿机制。
