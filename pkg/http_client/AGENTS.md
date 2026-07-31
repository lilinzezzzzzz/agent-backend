# AGENTS.md

`pkg/http_client/` 封装可复用的 httpx 异步 client、流式响应和下载。

- client 必须显式关闭或由 async context manager 管理；取消向上传播，不转换成普通失败结果。
- TLS verify、timeout、base URL 和默认 header 由构造参数提供，不为通过测试关闭证书校验。
- 网络失败、HTTP 错误和 JSON 解析失败保持可区分；日志不输出 Authorization、Cookie、query token、
  请求 body 或完整敏感 URL。
- 下载使用流式写入并明确失败时部分文件的处理；默认不重试非幂等请求。
