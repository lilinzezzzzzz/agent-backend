# AGENTS.md

`internal/services/agents/` 负责业务 Agent 路由、会话持久化、确认执行、流式事件和审计。

- 会话用例由 `conversation.py` 编排，数据库事务集中在 `storage.py`；`runtime.py` 适配通用运行协议，
  `execution.py` 管理 claim、lease、恢复与请求收尾，不在各领域 Service 复制状态机。
- 存在 `confirmation_token` 时，从服务端 pending action 恢复 route 和动作参数；确认执行绕过 LLM，不信任
  客户端重传的动作内容。
- `confirmation_token` 和 `idempotency_key` 分别承担授权动作绑定和重复提交控制；保留用户绑定、执行锁、
  冲突检测及结果复用。
- 会话、run、message 和 step 写入保持用户归属和事务边界，避免孤立 run 或部分历史。
- checkpoint 提交同时校验 lease fencing token 与 revision；步骤及终态消息与对应 checkpoint 原子写入，
  失去 lease 的旧执行者不得继续写入。受管理状态以主库为准，不从 Redis 或只读副本判断打断结果。
- 恢复保留原 run，先校验 checkpoint、definition version、冻结模型配置与临时确认 token；工具结果不确定且
  不可重放时进入 `recovery_required`，不能清空 checkpoint 后从头运行来宣称恢复成功。
- 请求取消先记录打断意图，在配置预算内收尾；超时或持久化失败时只报告已提交状态，不宣称工具已停止。
- 同步与流式入口保持错误码、route、run_id、事件顺序和最终结果一致，SSE 适配集中在 `stream.py`。
- 区分普通聊天与受管理 run：普通聊天拥有 run_id 不代表可以恢复；SSE 不承诺历史重放，保持 attempt 与
  checkpoint revision 的事件关联。边界说明见 `docs/agent/managed_run_lifecycle.md`。
- 审计 payload 递归脱敏；当前后台审计是 best-effort，不能描述为强审计。升级时需要 outbox、重试和幂等。
- 支付 Agent 继续保持只读；新增真实支付副作用必须先引入确定性 Workflow 和补偿机制。
