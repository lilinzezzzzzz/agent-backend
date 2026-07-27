# AGENTS.md

适用于 `internal/agents/payment/`。父级规则见 `internal/agents/AGENTS.md`，业务说明见本目录 `README.md`。

## 模块职责

本目录只实现支付支持 Agent 的 prompt、builder 和工具适配，支持支付方式、失败排查、扣款/账单/分期
知识咨询和整数分金额计算，不执行真实资金或账户副作用。

## 修改约束

- `builder.py` 只组装 `ReActAgent`、LLM action maker 和已注入的 `RagService`，不创建连接或读取全局业务状态。
- `prompt.py` 保持结构化 JSON 动作、单步动作、工具优先和明确拒绝副作用的约束。
- `tools.py` 只做参数解析、确定性计算和 Service 调用，不直接访问数据库、Redis、支付渠道或用户凭据。
- 支付方式、规则、知识检索和金额计算必须经过工具；缺参数或工具失败时不得让模型猜测结果。
- 金额统一使用非负整数分，优惠不得超过商品加运费；不要用 float 或 LLM 计算作为权威金额。
- 不新增真实支付、补扣款、撤销扣款、退款、解绑银行卡或账单修改工具。此类能力必须由独立
  Payment Service / Workflow 执行权限、幂等、事务、回调验签、对账和补偿。
- 工具名、参数 schema、路由范围或副作用边界变化时同步本目录 README、Agent Service、API schema 和测试。

## 验证重点

- 覆盖工具选择、缺参数、非法金额、优惠越界、RAG scope、非支付请求和副作用拒绝。
- 优先运行支付 Agent、统一 router、RAG Service 和相关 API 测试。
