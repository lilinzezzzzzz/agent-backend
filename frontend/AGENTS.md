# AGENTS.md

`frontend/` 保存 demo 前端；当前 Span 查看器依赖 `/v1/public/logger-span/traces/{trace_id}`。

- 保持单文件、自包含实现，除非任务明确要求构建工具或第三方前端依赖。
- API path、响应信封和 Span 字段变化时同步后端 contract；网络、空数据、异常 parent 和非法时间需要降级展示。
- 服务端数据使用 `textContent` 或等价安全方式写入 DOM，不把未可信内容拼进 `innerHTML`。
- 页面、URL、localStorage 和日志不得保存 token、secret 或敏感业务数据。
- 视觉或交互修改检查 loading、empty、error、success、键盘操作和窄屏状态。
