# AGENTS.md

适用于 `frontend/`。

## 目录职责

本目录保存 demo 前端；当前 `logger_span/index.html` 是无构建步骤的单文件 Span 查看器，依赖
`/v1/public/logger-span/traces/{trace_id}` contract。

## 修改约束

- 保持 HTML / CSS / JavaScript 自包含，除非用户明确要求引入构建工具或第三方前端依赖。
- API path、响应 envelope、Span 字段和状态枚举变化时，同步检查 Controller、schema、demo service
  和测试。
- 服务端数据必须使用 `textContent` 或等价安全方式写入 DOM，不把 trace、错误消息或 JSON 直接拼进
  `innerHTML`。
- 网络错误、非 JSON 响应、空 Span、孤立 parent 和时间字段异常要有明确降级展示。
- 不在页面、URL、localStorage 或日志中保存 token、secret 或敏感业务数据；公共 demo 不新增隐式认证绕过。
- 保持键盘操作、按钮状态和窄屏布局可用；视觉改动应检查 loading、empty、error 和 success 状态。

## 验证重点

- 修改内嵌 JavaScript 后做语法检查，并在浏览器中手工验证加载、错误、复制 URL 和 trace_id 初始化路径。
- 使用与 `SpanSimulateRespSchema` 一致的成功和失败 payload 对拍 waterfall、tree 与 raw JSON。
