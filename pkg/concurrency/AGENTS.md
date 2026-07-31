# AGENTS.md

`pkg/concurrency/` 负责进程内后台任务、同步函数 offload、并发限制、取消、超时和 shutdown 协作。

- 以 AnyIO 为主要并发抽象，不在包内建立独立 event loop 或隐藏线程/进程池生命周期。
- fire-and-forget 任务必须消费异常；取消、timeout、capacity limiter 和 shutdown 行为保持可观察。
- 同步阻塞函数只能通过明确的 offload 入口运行。
- 公开并发 API 的签名或资源所有权变化时检查所有调用方，并覆盖取消、超时、异常和清理路径。
