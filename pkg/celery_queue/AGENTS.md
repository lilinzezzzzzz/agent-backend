# AGENTS.md

适用于 `pkg/celery_queue/`。业务 task 约束见 `internal/tasks/AGENTS.md`。

## 模块职责

本模块封装通用 Celery app/client、任务投递、状态查询、撤销和 Worker 子进程生命周期 hook，
不承载业务任务名、幂等状态机或业务重试规则。

## 修改约束

- 不依赖 `internal/`；broker/backend、include、route、queue 和 lifecycle callback 由调用方注入。
- task name、queue、task_id、headers 与 trace_id 透传属于跨进程 contract；不得静默覆盖调用方 headers。
- async 方法必须 offload Celery 的同步阻塞 API，避免阻塞事件循环；取消语义和 timeout 要向调用方明确传播。
- Worker hook 在子进程内执行并用稳定 `dispatch_uid` 防重复注册；shutdown 清理必须在进程退出前完成。
- `revoke(terminate=True)` 可能中断正在执行的任务，不应成为默认行为，也不能替代业务取消状态机。
- 不在错误日志中输出完整任务 payload、凭据或敏感 headers。

## 验证重点

- 运行 `tests/celery/test_client.py` 和相关 Celery task 测试。
- 覆盖 queue/header 合并、显式 task_id、无效 trace_id、同步 API offload、startup/shutdown hook 和异常传播。
