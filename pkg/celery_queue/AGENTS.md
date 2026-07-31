# AGENTS.md

`pkg/celery_queue/` 封装通用 Celery client、任务投递和 Worker 生命周期，不承载业务任务规则。

- broker/backend、include、route、queue 和 lifecycle callback 由调用方注入。
- task name、queue、task_id、headers 和 trace_id 透传是跨进程 contract，不静默覆盖调用方 header。
- Celery 同步 API 在异步入口中 offload；取消和 timeout 向调用方传播。
- Worker 子进程 hook 使用稳定 `dispatch_uid` 防重复注册，并在退出前完成清理。
- `revoke(terminate=True)` 不作为默认取消方式，也不能替代业务取消状态机。
