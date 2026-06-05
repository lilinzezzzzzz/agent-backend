# AGENTS.md

适用于 `pkg/concurrency/`。父级约束见 `pkg/AGENTS.md`。

## 模块职责

`pkg/concurrency/` 是项目内并发执行基础包，负责进程内后台任务、同步阻塞函数 offload、
并发限制、取消、超时和 shutdown 协作。

- `background.py`：`asyncio` fire-and-forget 后台协程执行与异常消费。
- `offload.py`：同步函数在线程或子进程中的 `anyio` offload wrapper。
- `manager.py`：进程内 `AnyioTaskHandler`，管理后台协程、批量执行、取消和资源限制。
- `limits.py`：默认并发限制、超时和队列容量常量。

## 编码约定

- 不依赖 `internal/`，保持可被 `internal/` 与其他 `pkg/*` 复用。
- 保持 `anyio` 为主要并发抽象；不要混用自建线程池、进程池或裸 event loop lifecycle。
- 公共函数和类需要明确类型注解和 docstring。
- 后台任务必须显式处理异常，避免未消费 task result。
- 涉及 shutdown、cancel、timeout、capacity limiter 的行为变更必须同步测试。

## 兼容性要求

- `pkg.concurrency` 是顶层基础设施包；公开符号的重命名、删除、签名变更需要全仓检索
  调用方。
- 不在 `pkg/toolkit` 保留并发相关 re-export；调用方应直接依赖 `pkg.concurrency`。

## 验证重点

- 修改 `background.py` 优先运行 `tests/concurrency/test_background.py`。
- 修改 `manager.py` / `offload.py` 优先运行 `tests/concurrency/test_anyio_task.py`。
