# AGENTS.md

适用于 `pkg/scheduler/`。业务任务注册位置见 `internal/infra/scheduler/`。

## 模块职责

本模块封装进程内 AsyncIO APScheduler 生命周期、预启动 pending jobs 和 cron/interval/date 注册。

## 修改约束

- 不依赖 `internal/`；timezone、job defaults、trigger 和 callable 由调用方注入。
- `start()` / `shutdown()` 保持幂等，pending job 只在成功启动后加载；部分加载失败要可观察。
- job_id、replace_existing、coalesce、max_instances、misfire grace 和 jitter 会影响重复执行语义，
  修改默认值前检查调用方。
- 调度任务默认可能重复或补跑；有副作用的 callable 必须在业务层实现幂等和并发保护。
- 本实现是进程内调度器，不把内存 pending queue 描述为持久化或多实例协调方案。
- callback 异常应被记录但不得泄露参数中的 secret；shutdown 是否等待必须保持显式。

## 验证重点

- 运行 `tests/scheduler/test_scheduler.py`。
- 覆盖启动前注册、重复 start/shutdown、trigger 参数、replace、misfire/coalesce 和 callable 异常。
