# AGENTS.md

`pkg/embeddings/` 定义通用 Embedder、provider registry 和向量维度 contract。

- 凭据、URL、模型、dimension 和 timeout 通过构造函数注入；provider 差异集中在 registry/factory。
- `embed_texts()` 保持输入输出顺序一致；空批次不发网络请求，实际向量维度必须校验。
- timeout、provider error 和响应校验错误应可区分且不泄露 API key；取消原样传播。
- 批量大小、token 上限和重试必须有界，async 热路径不直接调用同步 SDK。
- dimension 或模型解析变化时同步配置和向量 collection schema。
