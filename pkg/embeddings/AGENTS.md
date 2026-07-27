# AGENTS.md

适用于 `pkg/embeddings/`。业务 provider 装配在 `internal/infra/embedding/`，向量使用方规则见
`pkg/vectors/AGENTS.md`。

## 模块职责

本模块定义通用 Embedder contract、provider registry、OpenAI-compatible 实现和向量维度错误。

## 修改约束

- 不依赖 `internal/`；API key、base URL、模型、dimension 和 timeout 通过构造函数注入。
- `embed_texts()` 返回顺序必须与输入顺序一致；空批次返回空列表，不发起网络请求。
- 实际向量维度必须校验。修改默认 dimension、provider 请求参数或模型名解析时同步 collection schema 和配置。
- 保持 `asyncio.CancelledError` 可传播；timeout、provider error 和响应校验错误要可区分且不得包含 API key。
- Provider 差异通过 registry / factory 扩展，不把 provider 名称判断散落到 repository 或业务层。
- 批量大小、token 上限和重试必须有界；不得在 async 热路径引入同步 SDK 调用。

## 验证重点

- 运行 `tests/embeddings/` 与相关 vector repository 测试。
- 覆盖空批次、顺序、维度不匹配、空响应、timeout、取消、请求参数和 key 脱敏。
