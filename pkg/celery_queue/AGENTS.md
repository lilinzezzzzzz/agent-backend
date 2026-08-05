# AGENTS.md

`pkg/celery_queue/` 封装通用 Celery client、任务投递和 Worker 生命周期机制，不承载应用装配或业务任务规则。

- broker/backend、include、route、queue 和 lifecycle callback 由调用方注入。
- `__init__.py` 不实例化或 re-export runtime 对象；调用方从职责模块显式导入。
- `client.py` 负责投递、查询和撤销；`lifecycle.py` 只负责通用 Worker signal 注册。
- 应用级 `celery_client` / `celery_app`、资源初始化、执行上下文和健康检查属于 `internal/infra/celery/`。
- 具体 task name、queue、timeout、include、route、beat schedule 和任务实现属于 `internal/tasks/`。
- task name、queue、task_id、headers 和 trace_id 透传是跨进程 contract，不静默覆盖调用方 header。
- Celery 同步 API 在异步入口中 offload；取消和 timeout 向调用方传播。
- Worker 子进程 hook 使用稳定 `dispatch_uid` 防重复注册，并在退出前完成清理。
- `revoke(terminate=True)` 不作为默认取消方式，也不能替代业务取消状态机。
