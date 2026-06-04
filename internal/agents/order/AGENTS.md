# AGENTS.md

适用于 `internal/agents/order/`。父级约束见 `internal/agents/AGENTS.md`。

## 模块职责

本目录只实现订单售后 Agent 的 prompt、builder 和工具适配。订单业务规则、权限校验、
待确认动作、幂等提交和缓存 key 由 `internal/services/order.py`、
`internal/services/agents/order.py` 和 `internal/cache/agent_action.py` 承担。

订单业务设计说明见本目录 `README.md`；本文件只记录维护约束，避免重复业务流程说明。

## 编码约定

- `builder.py` 只组装订单 Agent，不创建全局 Service 单例，不读取请求上下文。
- `prompt.py` 必须保持结构化 JSON 决策约束；新增工具时同步更新工具路由、禁止事项和最终回答要求。
- `tools.py` 只做工具参数适配和调用 `OrderService`；不要直接访问 ORM、Redis、Celery 或真实开票系统。
- 订单归属、存在性、开票参数、幂等和确认 token 校验必须由 `OrderService` 强制执行，不能只依赖 prompt。
- 金额相关工具必须使用整数分；不要让 LLM 自行计算退款金额、运费或优惠扣减。
- `prepare_invoice_request` 只能生成待确认动作，不能提交真实开票任务。
- 不要新增由 ReAct 直接执行取消订单、退款、支付或真实开票提交的工具；这类动作应封装为 Service /
  Workflow，并走显式确认和幂等路径。
- 非订单、物流、售后、退款或发票请求必须被拒绝或交给 router 降级，不应扩展到其他业务域。

## 文档同步

- 订单工具名、工具参数、入口路径、副作用边界、确认字段或幂等规则变化时，同步更新
  `internal/agents/order/README.md`。
- `README.md` 负责解释订单域流程和字段语义；本文件只维护代码修改约束，不复制 README 的流程图和字段表。
- 如果通用 Agent 规则变化，同步检查 `pkg/agents/README.md` 和父级 `internal/agents/AGENTS.md`。

## 验证重点

- 修改订单 prompt、builder 或 tools 时，优先运行：

  ```bash
  uv run pytest tests/agents/test_llm_decision.py tests/agents/test_react.py -q
  uv run pytest tests/api/test_agent.py -q -k 'not endpoint'
  uv run ruff check internal/agents/order internal/services/agents/order.py tests/agents tests/api/test_agent.py
  ```

- 修改 `prepare_invoice_request` 或确认语义时，补跑订单 Service 和 Agent action cache 相关测试。
