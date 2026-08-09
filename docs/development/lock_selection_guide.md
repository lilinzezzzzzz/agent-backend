# 乐观锁、悲观锁与 Redis 分布式锁选型指南

本文用于在项目内选择并发控制方式。当前行为以代码为准；本文只固化选择原则、适用边界和项目已有实现的使用约束。

## 快速选择

| 场景 | 优先选择 | 原因 |
| --- | --- | --- |
| 同一条业务数据可能被低频并发修改，允许失败后重试 | 乐观锁 | 不阻塞事务，适合读多写少和冲突概率低的路径 |
| 必须在数据库事务内串行化一段关键写入 | 数据库悲观锁 | 锁生命周期跟随事务，能和数据库写入保持同一原子边界 |
| 多进程或多实例需要短时间互斥，但关键资源不完全在同一数据库事务内 | Redis 分布式锁 | 获取成本低，适合跨进程协调短任务 |
| 需要保证资金、库存、订单状态等强一致写入 | 数据库约束 + 事务锁或乐观锁 | Redis 锁只能做协调，不能替代数据库一致性约束 |
| 后台 worker 批量领取任务 | `FOR UPDATE SKIP LOCKED` | 多 worker 可并发领取不同记录，避免相互等待 |

## 乐观锁

乐观锁假设冲突较少，不在读取时阻塞其他事务，而是在写入时检查数据是否仍是读取时的版本。常见做法是在业务表上增加 `version`、`revision` 或使用可控的 `updated_at` 条件：

```sql
UPDATE resource
SET value = :new_value, version = version + 1
WHERE id = :id AND version = :expected_version;
```

受影响行数为 `0` 表示版本已变化，调用方应返回冲突错误、重新读取后重试，或让用户重新确认。

适合：

- 读多写少，冲突概率低。
- 更新逻辑可以安全重试，或者可以把冲突反馈给调用方。
- 不希望长事务持有数据库锁。
- API 需要防止用户基于旧页面覆盖新数据。

不适合：

- 写入冲突频繁，重试会放大数据库压力。
- 关键区内包含不可重复执行的外部副作用。
- 业务要求进入关键区后必须独占执行，不能到提交时才发现失败。

项目现状：当前仓库没有通用乐观锁 helper。新增时应优先把版本字段放在具体业务模型上，并在 DAO / Service 层通过条件更新和受影响行数判断冲突，不要在 Controller 中直接拼接更新逻辑。

## 数据库悲观锁

悲观锁假设冲突需要提前阻塞或失败。项目内已有的通用实现是 `ScopedOperationLockService`，底层通过 `scoped_operation_locks` 唯一身份行上的 `SELECT ... FOR UPDATE` 获取 InnoDB 行锁：

- 模型：[`internal/models/scoped_operation_lock.py`](../../internal/models/scoped_operation_lock.py)
- DAO：[`internal/dao/scoped_operation_lock.py`](../../internal/dao/scoped_operation_lock.py)
- Service：[`internal/services/scoped_operation_lock.py`](../../internal/services/scoped_operation_lock.py)

它的语义是：

- `LockMode.WAIT` 使用 `SELECT ... FOR UPDATE`，等待锁释放。
- `LockMode.TRY` 使用 `SELECT ... FOR UPDATE NOWAIT`，拿不到锁时返回 `False`。
- 锁必须在显式 MySQL / MariaDB 事务内获取。
- 锁在事务 `commit` 或 `rollback` 后由数据库自动释放。
- 被保护的业务读写必须使用同一个 `AsyncSession` 和同一个事务。

适合：

- 同一业务资源需要严格串行处理，例如同一订单确认、同一资源状态迁移。
- 关键区主要是数据库读写，且可以放在同一事务内。
- 需要用事务提交 / 回滚作为锁释放边界。
- 冲突时等待比失败重试更简单、更符合业务语义。

不适合：

- 关键区包含长时间网络调用、模型调用、文件处理或用户交互。
- 需要跨多个数据库、Redis、消息队列和外部服务形成全局一致锁。
- 锁粒度过粗，会导致热点资源上的事务长时间排队。

使用注意：

- 进入锁后尽快完成数据库关键写入，避免在持锁事务内做慢操作。
- `operation_scope` 表示业务动作，`resource_key` 表示 scope 内唯一资源，粒度应尽量贴近实际冲突资源。
- 如果只是批量 worker 抢任务，优先使用任务表上的 `FOR UPDATE SKIP LOCKED` 模式，而不是通用互斥锁。

## Redis 分布式锁

项目内 Redis 锁由 `RedisClient.acquire_lock()` 和 `RedisClient.release_lock()` 提供：

- 实现：[`pkg/redis/client.py`](../../pkg/redis/client.py)
- 当前业务使用：[`internal/cache/agent_action.py`](../../internal/cache/agent_action.py)

获取锁使用 Redis `SET key identifier NX PX expire_ms`。释放锁使用 Lua 脚本先比较 owner identifier，再删除 key，避免误删其他调用方已重新获取的锁。

适合：

- 多进程、多实例之间做短时间互斥。
- 任务可以通过 TTL 自动兜底释放锁。
- 保护的是短流程协调，例如防止同一个确认 token 被并发执行。
- 失败后可以通过幂等记录、数据库状态或补偿逻辑恢复。

不适合：

- 单靠 Redis 锁保证数据库强一致性。
- 业务执行时间不可预测，可能超过 TTL。
- 需要严格 fencing token、防止锁过期后旧 owner 继续写入。
- Redis 不可用时业务仍必须正确串行执行。

使用注意：

- `expire_ms` 必须大于正常关键区耗时，并保留网络抖动余量，但不要无限延长。
- `timeout_ms` 和重试间隔要有上限，避免请求线程长期堆积。
- 锁过期不等于业务完成；关键写入仍应有数据库唯一约束、状态机条件、幂等记录或 fencing 校验。
- 当前实现是单 Redis 实例语义，不是 Redlock。

## 组合原则

- 数据库内的不变量由数据库负责：唯一索引、条件更新、事务、行锁是最终防线。
- Redis 锁只作为跨进程协调层，不能替代数据库约束。
- 乐观锁适合低冲突更新；悲观锁适合必须先独占再写入；Redis 锁适合短时间跨实例互斥。
- 外部副作用要配合幂等键、状态机或 outbox，不能只依赖任何一种锁。
- 先缩小锁粒度，再选择锁类型；锁住用户、租户或全局资源通常会制造不必要的热点。

## 决策问题

新增并发控制前，先回答这些问题：

- 冲突资源的最小唯一键是什么？
- 关键写入是否都在同一个数据库事务里？
- 冲突时应该等待、立即失败，还是重试？
- 锁持有期间是否会调用外部服务或执行慢任务？
- 锁过期、进程崩溃、数据库回滚时，业务状态如何恢复？
- 是否已有唯一约束、状态机条件或幂等记录作为最终一致性保护？
