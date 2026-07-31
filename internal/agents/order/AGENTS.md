# AGENTS.md

`internal/agents/order/` 只适配订单售后 Agent；订单规则和确认提交由 `OrderService` 及 Agent Service 强制执行。

- 工具不得直接访问 ORM、Redis、Celery 或真实开票系统；订单归属、状态、幂等和确认 token 不能只依赖 prompt。
- 金额统一使用整数分，LLM 不作为退款、运费或优惠计算的权威来源。
- `prepare_invoice_request` 只能生成 pending action；取消、退款、支付和真实开票必须走确定性确认 Workflow。
- 非订单、物流、售后、退款或发票请求交给统一 router 降级，不扩展本 Agent 的业务域。
- 工具名、参数和副作用边界变化时同步本目录 README、Service 和相关测试。
