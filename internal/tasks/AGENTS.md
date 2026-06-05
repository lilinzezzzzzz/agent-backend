# AGENTS.md

适用于 `internal/tasks/`。

## 层职责

本层定义 Celery 任务、任务路由和定时调度。任务可复用 Service/DAO，但不应复制 Controller 逻辑。

## 编码约定

- Celery app 在 `internal/infra/celery/__init__.py` 暴露，任务模块统一放在本目录下，供 Worker 和 Beat 调用。
- task name 必须稳定，避免影响已投递消息、监控和定时任务。
- 新任务要明确队列、参数 schema、重试策略、幂等键、超时和日志上下文。
- 异步业务逻辑通过项目已有 `run_in_async` 或 Celery 工具封装调用。
- 任务中使用 DB/Redis 时依赖 worker hook 初始化的连接，不自行创建重复连接池。
- 定时任务变更同步检查 `scheduler.py` 和 worker 启动队列。

## 可靠性要求

- 任务可能重复执行，默认按 at-least-once 语义设计。
- 外部 I/O 要考虑超时、重试、部分失败和可观测日志。
- 批量任务严禁逐条数据库查询或逐条提交；使用批量读取和批量写入。

## 验证重点

- 纯逻辑优先单元测试。
- 需要 Redis/Celery Worker 的测试标记为 integration，并在结果中说明外部依赖是否运行。
