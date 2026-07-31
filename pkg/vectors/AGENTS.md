# AGENTS.md

`pkg/vectors/` 定义向量检索 contract、后处理、repository 和 backend 边界。

- 通用类型和 repository 不泄漏 Milvus SDK 类型；阻塞 backend 调用通过既有 offload 入口执行。
- ids、metadata、filters、score、search mode 和排序语义在 backend 间保持一致。
- collection schema、metadata 编码和 filter 语义是持久化 contract；默认 top_k、candidate_top_k、ranker、
  full-text 或 hybrid 行为变化时同步调用方和测试。
- 外部服务不可用时单元测试使用 mock；真实 backend 行为只在显式 integration 环境验证。
