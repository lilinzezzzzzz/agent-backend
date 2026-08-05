# AGENTS.md

`internal/infra/celery/` 是当前应用的 Celery composition root 和运行时适配层。

- `application.py` 是唯一实例化 `celery_client`、导出 `celery_app` 并注册 Worker hook 的模块；Celery CLI 显式指向该模块。
- `lifecycle.py` 管理 Worker 子进程的配置、日志和 tracing；应用资源 callback 通过 `pkg.celery_queue.lifecycle` 注册。
- `execution.py` 管理同步任务调用异步服务时的事件循环、DB、Redis 和 request context，不承载调度周期或业务规则。
- `health.py` 只提供 API 进程使用的 broker 健康检查；失败不得隐式改变 Worker 或任务状态。
- `__init__.py` 不实例化或 re-export runtime 对象，调用方从上述职责模块显式导入。
- task name、queue、timeout 和任务实现放在 `internal/tasks/`；include、route 和 beat schedule 以 `internal/tasks/scheduler.py` 为源。
