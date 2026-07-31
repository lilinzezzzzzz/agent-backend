# AGENTS.md

`pkg/request_context/` 使用 ContextVar 保存 trace_id、user_id 和少量执行上下文。

- Web 请求由最外层 recorder middleware 初始化并在结束时清理；未初始化访问继续明确失败，不提供隐式身份默认值。
- 新公共 key 加入 `ContextKey`，明确类型、写入 owner、清理点和后台任务传播策略。
- 创建后台任务时判断是否允许继承用户和 trace 上下文，避免跨请求复用 mutable 数据。
- 上下文不保存 token、密码、密钥或大对象；Worker 等非 Web 路径显式 init/clear。
- 变化需覆盖并发隔离、task 继承、缺失身份和清理路径。
