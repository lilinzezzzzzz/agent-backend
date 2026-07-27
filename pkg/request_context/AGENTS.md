# AGENTS.md

适用于 `pkg/request_context/`。

## 模块职责

本模块使用 ContextVar 保存当前执行上下文的 trace_id、user_id 和扩展字段，供 middleware、logger、
tracing 和 Service 读取。

## 修改约束

- Web 请求由最外层 recorder middleware 初始化并在结束时清理；`set_*` 在未初始化时继续明确失败。
- `get_user_id()` / `get_trace_id()` 的缺失异常和有效性校验是权限与可观测性边界，不改为隐式默认值。
- 新增公共 key 统一加入 `ContextKey`，说明类型、写入 owner、清理点和后台任务传播策略。
- ContextVar 会随 asyncio task 复制；创建后台任务时确认是否允许继承用户/trace 上下文，避免跨请求
  复用 mutable dict。
- 不把 token、密码、密钥或大对象放入上下文，也不要在异常中打印完整上下文。
- 非 Web / Worker 路径需要上下文时显式 init/clear，不依赖某次历史请求留下的值。

## 验证重点

- 运行 `tests/request_context/`、middleware、logger 和 tracing 相关测试。
- 覆盖未初始化、初始化/清理、并发隔离、task 继承、非法 trace_id 和缺失 user_id。
