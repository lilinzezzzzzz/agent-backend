# 数据库连接生命周期与事务锁归属

数据库的 engine 和 session maker 由 [Infra 连接入口](../../internal/infra/database/connection.py) 管理。
[公共工厂](../../pkg/database/session.py) 只构造资源，生命周期由调用方负责。
本模块不额外创建 runtime、操作租约或驱动句柄追踪，也不装饰 SQLAlchemy dialect 方法。

## 实际操作锁的归属

本仓库的 [ScopedOperationLockService](../../internal/services/scoped_operation_lock.py) 使用
`scoped_operation_locks` 身份行上的 PostgreSQL `SELECT ... FOR UPDATE` 实现业务互斥。
锁属于调用方的 session 和事务，在 commit/rollback 时由 PostgreSQL 自动释放；未显式结束的事务会随
session 关闭而回滚。连接池的 `dispose()` 不承担业务锁释放职责，不能为等待池关闭而延长业务事务。
使用方式见 [锁选型指南](lock_selection_guide.md)。

API lifespan 和 Celery 执行器没有另一个持有数据库运行／维护锁的 runtime，因此不存在需要在这两个入口
退出后额外释放的数据库操作锁。本次只落实已有事务与连接生命周期的顺序，不增加新的锁。

## 关闭顺序与责任

1. 生命周期所有者先结束自己管理的业务工作。事务提交或回滚，释放业务行锁。
2. 等待 session 关闭、连接归还。`get_session()` / `get_read_session()` 保留调用方控制事务的行为，
   成功路径不自动提交；未配置只读副本时回退主库。
3. 所有者调用并等待 `close_async_db()`，分别尝试只读池与主库的 `await engine.dispose()`。
4. 数据库清理结束后，所有者才退出其应用／任务运行上下文和事件循环；清理失败以异常传播。

[API lifespan](../../internal/app.py) 已经先退出受管理的后台任务，再通过现有
[close_resources()](../../internal/infra/lifecycle.py) 等待连接等资源清理。
[Celery run_in_async()](../../internal/infra/celery/execution.py) 在业务协程结束后的 finally 中等待清理，
再结束 `anyio.run()` 创建的事件循环。启动链先登记清理，覆盖部分初始化失败。
这两处所有权和已有取消保护保持不变，不为数据库新增一套并发关闭协议。

session 和 revision 检查取得的连接也通过已有 `close_resources()` 等待其关闭，避免调用方取消后
清理在后台晚于池 dispose 完成。关闭由生命周期所有者顺序调用；调用方必须先等待所有持有 session 的工作结束。

## 失败与边界

- 只读池 dispose 抛错仍尝试主库。单个错误保留原异常，多个错误通过 `BaseExceptionGroup` 完整报告。
- 清理开始后清除 session maker，不再发放新 session；成功关闭的 engine 清除引用，失败 engine 保留供
  所有者顺序重试。初始化和同步 reset 不能绕过尚未关闭的 engine，reset 不代替异步 dispose。
- 取消保护复用现有 `close_resources()`：等待本轮清理结束后，再传播调用方的取消或清理错误。
- 使用标准 SQLAlchemy 生命周期，不接管驱动或池内部吞掉的异常、不承诺任意部分失败后的逐句柄恢复。
  `dispose()` 不会强制回收仍被借出的连接，因此不能代替上面的业务工作与 session 收尾。

## 验证

[数据库关闭测试](../../tests/lifecycle/test_database_lifecycle.py) 覆盖两个池的独立清理、错误传播、
取消期间等待 session 和连接池清理，以及 Celery 退出事件循环前的实际 dispose 调用。
已有 API/Celery 生命周期测试继续覆盖后台任务排空、部分启动失败和重复取消。

```bash
uv run --no-sync --group dev pytest tests/lifecycle tests/infra/test_sqlite_database.py tests/infra/test_database_safety.py -q
```

真实 PostgreSQL 验证复用现有迁移集成测试的一次性容器和参数，不另建 Docker 测试框架。
仅在获准的本机 Docker 环境中运行，提前准备 `postgres:17` 镜像；夹具不连接工作区已有数据库。

```bash
uv run --no-sync --group dev pytest tests/orm/test_migrations_integration.py::test_application_transaction_lock_releases_before_session_and_pool_close --run-postgresql-migrations -q
```

该测试验证 commit/rollback 后竞争事务能获取真实业务锁，然后确认 session 关闭、池 dispose 和对应
PostgreSQL 后端 PID 消失。它不将业务行锁描述成运行时锁，也不代表主从切换或任意驱动故障的恢复验收。
