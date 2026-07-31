# AGENTS.md

适用于 `internal/middlewares/`。

## 层职责

本层处理 ASGI 请求链上的横切能力，包括请求记录、请求上下文生命周期、endpoint guard 和统一异常处理。
Token 与内部签名认证由 `internal/dependencies/auth.py` 按 Router 边界执行，不在本层重复实现。

## 编码约定

- 中间件必须保持 ASGI 兼容，正确处理 HTTP 和非 HTTP scope。
- 上下文写入要在请求结束后清理，避免并发请求串数据。
- 日志记录应包含 trace、path、method、status、耗时等排障信息，但不能泄漏敏感数据。
- 异常转换要保持统一错误结构和稳定错误码。

## 性能和安全

- 不要在中间件中做重型业务查询、外部长耗时调用或无界读取 request body。
- endpoint guard 命中后要快速返回，避免进入业务 handler。
- 对 body、headers、query 的记录要做脱敏和大小限制。

## 验证重点

- 覆盖 endpoint guard、请求记录、异常转换和响应已开始等分支。
- 并发或上下文相关变更要测试 trace/user_id 是否隔离；认证行为测试放在 API dependency 对应测试中。
