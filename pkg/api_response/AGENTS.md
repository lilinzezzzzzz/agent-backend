# AGENTS.md

`pkg/api_response/` 定义统一响应 payload、错误 HTTP response 和 SSE 包装，是 API wire contract。

- `code`、`message`、`data` 及分页的 `items/page/limit/total` 字段保持稳定。
- 正常 Controller 返回 `ResponsePayload` 并由 FastAPI `response_model` 校验；直接 `JSONResponse` 仅用于异常
  处理等直接响应路径。
- 成功数据只接受模块明确支持的类型，不隐式序列化 ORM 或任意对象。
- SSE event/data block 以空行结束，事件名和 JSON 数据不得引入未转义换行或旁路错误结构。
- 错误详情不得包含凭据、内部堆栈或敏感 payload。
