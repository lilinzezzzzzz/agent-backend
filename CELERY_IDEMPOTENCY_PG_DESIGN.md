# Celery PostgreSQL 状态机设计

本文档描述当前 `celery_task_record` contract。该实现以 PostgreSQL 为任务事实源，Celery Broker 只负责消息投递，
不使用 result backend 推断业务状态。

## 设计边界

- 状态机时间统一来自当前主库事务 session 的 `ModelMixin.get_database_now()`。
- 数据库存储 UTC naive `TIMESTAMP WITHOUT TIME ZONE`；数据库和 session 必须配置为 UTC。
- 幂等键在 `(scope, task_name, idempotency_key_hash)` 范围内永久有效。
- 相同幂等键和相同 `payload_hash` 返回已有任务；payload 不同返回 `IdempotencyConflict`。
- 不自动重投失败任务，不使用进程内锁，不依赖 Celery result backend。
- 当前没有 fence/orphan 隔离机制。任务的外部副作用必须由业务自身保证幂等或隔离。

这是 breaking schema。新表 DDL 位于：
`ddl/postgresql/1.2.0_celery_task_state_machine.sql`。已有环境必须人工确认并删除旧表后执行；脚本本身不会删除旧表、
迁移旧数据或执行回填。

## 状态机

```text
SUBMITTING ── broker confirmed ──► QUEUED ── worker claim ──► RUNNING ──► SUCCEEDED
     │                              │                          │          FAILED
     │ publish failed/timeout       │ queue start timeout      │ cancel
     ▼                              ▼                          ▼
   FAILED                         FAILED                   CANCELLING ──► CANCELLED
     │                              │
     └──── cancel before terminal ──┴────────────────────────► CANCELLED
```

允许的业务转换：

| 当前状态 | 事件 | 下一状态 |
|---|---|---|
| `SUBMITTING` | Broker 接受消息 | `QUEUED` |
| `SUBMITTING` / `QUEUED` | Worker 成功 claim | `RUNNING` |
| `SUBMITTING` | Broker 异常 | `FAILED` |
| `RUNNING` | token 匹配的 Worker 完成 | `SUCCEEDED` / `FAILED` |
| `SUBMITTING` / `QUEUED` | 用户取消 | `CANCELLED` |
| `RUNNING` | 用户取消 | `CANCELLING` |
| `CANCELLING` | Worker 协作确认 | `CANCELLED` |

`CANCELLING` 和 `CANCELLED` 的重复取消幂等成功；`SUCCEEDED` 和 `FAILED` 不允许取消，API 返回
`TaskStateConflict`。

## 时间与 deadline

跨进程状态转换在每个事务内只读取一次数据库时间，并复用该值设置时间字段和 CAS 条件：

- `queued_deadline_at = database_now + CELERY_QUEUE_START_TIMEOUT_SECONDS`
- `hard_deadline_at = database_now + Celery task time limit + CELERY_EXECUTION_DEADLINE_GRACE_SECONDS`
- `started_at` 只在首次成功 claim 时设置。
- `finished_at` 在进入 `SUCCEEDED`、`FAILED` 或 `CANCELLED` 时设置。

`queued_deadline_at` 不是 Celery 消息的 `expires`。它只用于识别 Broker 已接受、但 Worker 未及时 claim 的任务。

## 提交与快速消费竞态

1. API 在主库事务中写入 `SUBMITTING`，同时保存真实 queue。
2. 事务提交后直接调用 `CeleryClient.async_submit()`，不设置人为 `countdown`。
3. Broker 成功后，API 以 `status = SUBMITTING` 为条件 CAS 到 `QUEUED`。
4. Worker 可从 `SUBMITTING` 或 `QUEUED` claim，因此快速消费不会被发布确认回写阻塞。
5. 如果 Worker 已进入 `RUNNING`，API 的 `mark_queued` CAS 自然失败并返回数据库当前状态，不覆盖 Worker 状态。
6. Broker 调用抛异常时，只把仍为 `SUBMITTING` 的记录更新为 `FAILED/BROKER_PUBLISH_FAILED`；若 Worker 已抢先
   claim，则保留数据库当前状态。

## 执行门禁

每次 delivery 生成新的 `execution_token`。claim 使用单条条件更新：

- 匹配 `record_id + task_name + scope`；
- 当前状态必须是 `SUBMITTING` 或 `QUEUED`；
- 写入 `RUNNING`、`execution_token` 和 `hard_deadline_at`；
- 原子递增 `attempt_count`。

完成写入必须同时匹配 `RUNNING + execution_token`。重复 delivery、晚到 Worker 或 token 不匹配的 Worker 不能覆盖
新状态。

任务失败只持久化稳定 `error_code` 和脱敏 `error_summary`，原始异常详情只进入受控 Worker 日志。

## 协作取消

`POST /celery-tasks/{record_id}/cancel` 先提交数据库状态，再 best-effort 调用：

```python
await celery_client.async_revoke(task_id, terminate=False)
```

revoke 失败只记录脱敏告警，不回滚数据库状态。运行中的 Worker 在业务执行前后检查 `CANCELLING`，以
`record_id + execution_token` CAS 到 `CANCELLED` 后抛出 Celery `Ignore`。当前实现不使用 `terminate=True`，因此长任务
应在业务阶段边界增加更多取消检查点。

## Reconciler

三个 Beat task 分别执行批量 CAS，候选记录均通过 `FOR UPDATE SKIP LOCKED` 获取：

| 扫描条件 | 结果 | 错误码 |
|---|---|---|
| stale `SUBMITTING` | `FAILED` | `PUBLISH_CONFIRMATION_TIMEOUT` |
| `QUEUED` 且 queue deadline 到期 | `FAILED` | `QUEUE_START_TIMEOUT` |
| `RUNNING` 且 hard deadline 到期 | `FAILED` | `WORKER_LOST_OR_TIMEOUT` |
| `CANCELLING` 且 hard deadline 到期 | `CANCELLED` | `CANCEL_DEADLINE_EXCEEDED` |

Reconciler 只收敛数据库状态，不重新发布消息，也不能保证 deadline 到期后的旧 Worker 已停止外部副作用。

`NEEDS_RECONCILIATION` 不属于持久化状态。是否需要收敛由当前状态和 deadline 条件派生，例如
`status = 'QUEUED' AND queued_deadline_at <= database_now`。这样 `status` 只表达任务生命周期，后台治理动作不会引入
额外中间状态。

## 配置

```dotenv
CELERY_IDEMPOTENCY_ENABLED=true
CELERY_EXECUTION_DEADLINE_GRACE_SECONDS=30
CELERY_QUEUE_START_TIMEOUT_SECONDS=300
CELERY_PUBLISH_CONFIRM_TIMEOUT_SECONDS=30
CELERY_PUBLISH_RECONCILER_INTERVAL_SECONDS=10
CELERY_RECONCILER_BEAT_ENABLED=true
CELERY_RECONCILER_INTERVAL_SECONDS=60
CELERY_RECONCILER_BATCH_SIZE=100
CELERY_RECONCILER_QUEUE=celery_maintenance_queue
CELERY_TASK_MAX_PAYLOAD_BYTES=262144
```

安全边界：

- `CELERY_EXECUTION_DEADLINE_GRACE_SECONDS <= 300`
- `CELERY_QUEUE_START_TIMEOUT_SECONDS > CELERY_RECONCILER_INTERVAL_SECONDS`
- `CELERY_PUBLISH_CONFIRM_TIMEOUT_SECONDS > CELERY_PUBLISH_RECONCILER_INTERVAL_SECONDS`
- `CELERY_RECONCILER_QUEUE` 不能为空

## API contract

- `POST /v1/celery-tasks/idempotent-sum/create`
- `GET /v1/celery-tasks/{record_id}`
- `POST /v1/celery-tasks/{record_id}/cancel`

创建接口可能返回 `SUBMITTING`、`QUEUED` 或 `RUNNING`，取决于 Broker 回写和快速 Worker claim 的竞态结果。查询接口
返回 queue、attempt、deadline、开始/结束时间以及脱敏错误字段。
