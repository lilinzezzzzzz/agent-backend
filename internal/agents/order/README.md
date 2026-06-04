# 订单售后 Agent

本目录实现订单售后业务 Agent，负责订单事实查询、售后规则解释、订单知识检索、
退款金额计算，以及开票申请的两阶段确认准备。

通用 ReAct 执行循环和 Agent 选型原则见 `pkg/agents/README.md`。本文件只记录订单域
的业务工具、路由入口和副作用处理约定。

## 入口与职责

当前订单 Agent 可通过两个 API 入口进入：

- `/v1/agent/chat`：统一 Agent 入口，由 `AgentRouterService` 判断是否路由到订单 Agent。
- `/v1/agent/order/support`：直接进入订单售后 Agent。

代码职责划分：

| 文件 | 职责 |
| --- | --- |
| `builder.py` | 组装 `ReActAgent`、订单 prompt 和订单工具 |
| `prompt.py` | 约束订单 Agent 的工具路由、禁止事项和最终回答格式 |
| `tools.py` | 将订单业务能力适配为 `StructuredTool` |
| `internal/services/agents/order.py` | 面向 Controller 的订单 Agent Service |
| `internal/services/agents/router.py` | 统一 Agent 路由 Service |
| `internal/services/order.py` | 订单权限校验、待确认动作和开票确认提交 |
| `internal/cache/agent_action.py` | confirmation token、确认结果、幂等结果和执行锁的 Redis key 约定 |

## 工具边界

适合由订单 ReAct Agent 动态选择的工具：

```text
get_order_status            # 查询当前用户有权访问的订单事实
get_return_policy           # 查询售后退货规则
search_order_knowledge      # 检索订单、物流、售后和发票知识
calculate_refund_amount     # 使用整数分精确计算退款金额
prepare_invoice_request     # 只生成服务端待确认动作，不执行真实开票提交
```

工具边界要求：

- 订单事实、业务规则、知识库内容、精确金额计算和开票申请都必须先调用工具。
- 缺少必填参数时，Agent 应返回 final 询问用户，不得猜测。
- 非订单、物流、售后、退款或发票相关请求应拒绝处理，不得调用订单工具。
- `prepare_invoice_request` 只能在用户明确要求提交开票申请时调用；咨询开票规则时不得准备申请。

## 订单域副作用

本设计中的“副作用”指请求执行后会改变系统状态、触发外部动作，或产生后续不可简单撤销
影响的操作。它和同步/异步无关。

订单域内的副作用分为两类：

| 类型 | 示例 | 当前处理 |
| --- | --- | --- |
| 可丢弃的临时副作用 | 保存 pending action、写入短期 `confirmation_token` | 由 `prepare_invoice_request` 生成，TTL 到期失效 |
| 真实业务副作用 | 提交开票申请、取消订单、退款、支付、发通知、创建会被 worker 消费的任务 | 必须由 `OrderService` 或 Workflow 执行权限校验、幂等、事务和补偿 |

只读查询、知识检索、金额纯计算和组织回答不属于真实业务副作用。

## 开票两阶段确认

开票申请采用 prepare / confirm 两阶段执行：

```text
用户明确提出开票请求
→ ReAct 调用 prepare_invoice_request
→ OrderService 校验订单归属，并在 Redis 保存短期待确认动作
→ API 返回一次性 confirmation_token 和动作摘要
→ 客户端展示摘要，用户确认后携带 confirmation_token + idempotency_key 再次请求
→ OrderService 绕过 LLM，按 confirmation_token 加锁并幂等提交
→ 返回 queued；相同 token 不允许使用不同幂等键重复执行
```

`prepare_invoice_request` 只产生可丢弃的临时副作用：保存服务端 pending action。它不会
提交真实开票任务，也不能在最终回答中声称“已提交”。

`confirm_invoice_request` 才执行真实业务副作用：读取服务端 pending action，校验用户和
动作类型，然后受理开票申请。确认阶段必须绕过 LLM，不能相信客户端或 LLM 重传的订单号、
抬头、税号、邮箱等业务参数。

## confirmation_token 与 idempotency_key

确认请求中的两个字段职责不同，不能互相替代：

| 字段 | 生成方 | 作用 | 不承担 |
| --- | --- | --- | --- |
| `confirmation_token` | 服务端 | 指向服务端保存的待确认动作，绑定用户、动作类型和动作参数；确认时作为锁粒度，保证同一个动作只被执行一次 | 不处理客户端重试语义，不应由客户端或 LLM 构造 |
| `idempotency_key` | 客户端 | 标识一次确认提交请求；网络超时、客户端重试或网关重放时，返回同一执行结果 | 不描述业务动作，不提供权限，不允许绕过 `confirmation_token` 执行写操作 |

可以简化理解为：

```text
confirmation_token = 确认哪一个服务端已准备好的动作
idempotency_key    = 这一次确认提交请求是否已经处理过
```

确认路径先按 `confirmation_token` 加锁并检查确认结果，再检查 `idempotency_key` 对应结果：

- 相同 `confirmation_token` + 相同 `idempotency_key`：返回同一提交结果。
- 相同 `confirmation_token` + 不同 `idempotency_key`：拒绝，避免同一个待确认动作被换键重复执行。
- 相同 `idempotency_key` + 不同 `confirmation_token`：拒绝，避免客户端重试键被错误复用到另一个动作。

生产接入真实开票系统时，Redis 幂等缓存不能替代最终一致性设计。仍应使用数据库唯一约束、
transactional outbox 或等价机制，保证任务记录和消息投递原子性。
