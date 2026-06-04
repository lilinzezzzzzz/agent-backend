# 支付支持 Agent

本目录实现支付支持业务 Agent 的最小架构，负责支付方式说明、付款失败排查、
扣款异常解释、账单和分期规则咨询，以及应付金额的整数分计算。

当前支付 Agent 只处理咨询和纯计算，不发起真实支付、补扣款、退款提交、解绑银行卡
或修改账单。真实支付副作用后续必须由支付 Service / Workflow 执行权限校验、幂等、
事务、渠道回调校验和对账补偿，确认路径应绕过 LLM。

## 入口与职责

当前支付 Agent 可通过两个 API 入口进入：

- `/v1/agent/chat`：统一 Agent 入口，由 `AgentRouterService` 判断是否路由到支付 Agent。
- `/v1/agent/payment/support`：直接进入支付支持 Agent。

代码职责划分：

| 文件 | 职责 |
| --- | --- |
| `builder.py` | 组装 `ReActAgent`、支付 prompt 和支付工具 |
| `prompt.py` | 约束支付 Agent 的工具路由、禁止事项和最终回答格式 |
| `tools.py` | 将支付咨询和金额计算能力适配为 `StructuredTool` |
| `internal/services/agents/payment.py` | 面向 Controller 的支付 Agent Service |
| `internal/services/agents/router.py` | 统一 Agent 路由 Service |

## 工具边界

适合由支付 ReAct Agent 动态选择的工具：

```text
get_supported_payment_methods  # 查询支持的支付方式
search_payment_knowledge       # 检索付款失败、扣款异常、账单和分期知识
calculate_payment_total        # 使用整数分精确计算应付金额
```

工具边界要求：

- 支付规则、渠道能力、知识库内容和精确金额计算都必须先调用工具。
- 缺少必填参数时，Agent 应返回 final 询问用户，不得猜测。
- 非支付相关请求应拒绝处理，不得调用支付工具。
- 不要新增由 ReAct 直接执行真实支付、退款、补扣款、解绑银行卡或账单修改的工具。
