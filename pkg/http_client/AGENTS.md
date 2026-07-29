# AGENTS.md

适用于 `pkg/http_client/`。

## 模块职责

本模块封装可复用的 httpx 异步长连接 client、统一 `RequestResult`、流式响应和文件下载。

## 修改约束

- client 必须显式关闭或通过 async context manager 使用；不在 import 时创建网络连接。
- timeout、TLS verify、base URL 和默认 headers 通过构造参数提供；不要为通过测试默认关闭证书校验。
- 保持 `RequestResult.success/status_code/error/response/json()` 语义稳定；网络失败、HTTP 错误和
  JSON 解析失败可区分。
- `asyncio` / AnyIO 取消必须传播，不转换为普通 500 结果。
- 日志不得输出 Authorization、Cookie、query token、请求 body 或完整敏感 URL。
- 下载使用流式写入；调用方负责 save path 授权。失败时部分文件保留/清理行为必须显式并有测试。
- 不默认重试非幂等请求；新增重试需限制次数、退避、可重试错误和 method 范围。

## 验证重点

- 运行 `tests/http_client/test_http_client.py` 和第三方认证 client 测试。
- 覆盖 2xx/4xx/5xx、timeout、连接错误、取消、流式关闭、下载进度和 client close。
