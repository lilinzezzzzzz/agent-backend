# AGENTS.md

`pkg/scheduler/` 是进程内 AsyncIO APScheduler 封装，不是持久化或多实例协调方案。

- `start()` / `shutdown()` 保持幂等，启动前 pending job 只在成功启动后加载。
- job ID、replace、coalesce、max instances、misfire 和 jitter 会改变重复执行语义，变更默认值前检查调用方。
- 调度任务可能重复或补跑；有副作用的 callable 在业务层实现幂等和并发保护。
- callback 异常可观察且不泄露参数，shutdown 是否等待保持显式。
