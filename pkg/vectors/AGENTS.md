# AGENTS.md

适用于 `pkg/vectors/`。

## 层职责

本目录提供向量检索抽象、检索后处理、上下文组装、repository 和后端实现。它面向 RAG、混合检索和向量存储适配。

## 编码约定

- 通用契约放在 `contracts.py` 和 `repositories/`，后端细节放在 `backends/`。
- backend 实现必须遵守统一 contract，不把 Milvus 特有字段泄漏到通用层，除非通过明确扩展类型表达。
- 阻塞 SDK 调用应使用现有 anyio/thread 封装，避免阻塞 async 热路径。
- 搜索参数、score、filters、metadata、ids 的语义要保持跨 backend 一致。
- 修改 Milvus 行为前先读对应 backend 的 `README.md`。

## 兼容性要求

- collection schema、metadata 编码、filter 语义和搜索模式属于兼容性边界。
- 修改默认 top_k、candidate_top_k、ranker、full text 或 hybrid 行为时，要同步文档和测试。
- 外部服务不可用时，单元测试应使用 mock；真实 Milvus 只放 integration 测试。

## 验证重点

- 通用逻辑运行 `tests/vector/` 中非外部依赖测试。
- Milvus 集成测试需要确认 Milvus 服务状态和相关环境变量后再运行。
