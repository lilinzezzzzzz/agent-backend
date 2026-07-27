# AGENTS.md

适用于 `pkg/api_response/`。Controller 使用约束见 `internal/controllers/AGENTS.md`。

## 模块职责

本模块定义应用状态码载体、统一响应 payload、错误 HTTP response 和 SSE 文本包装，是 API wire contract
的共享基础。

## 修改约束

- `code`、`message`、`data` 信封字段和应用错误码语义保持稳定；修改前全仓检查 schema、middleware、
  SSE 和前端。
- 正常 Controller 使用 `ResponsePayload` 与 FastAPI `response_model`；`JSONResponse` 仅用于异常处理等
  直接响应路径。
- 成功数据只接受明确支持的 dict、list、Pydantic model 或 `None`，不要静默序列化任意对象或 ORM model。
- 分页字段 `items`、`page`、`limit`、`total` 属于公开 contract，变更需要迁移调用方。
- SSE 保持每个 event/data block 以空行结束；事件名和 JSON 数据不得引入未转义换行或旁路错误结构。
- 错误详情可排障但不得包含 token、secret、密码、连接串或内部堆栈。

## 验证重点

- 运行 `tests/test_json_response.py`、schema 与 API 错误响应测试。
- 覆盖 Pydantic/list 展开、非法成功数据、分页、语言回退和 SSE 格式。
