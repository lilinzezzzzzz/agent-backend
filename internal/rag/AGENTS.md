# AGENTS.md

适用于 `internal/rag/`。通用向量 contract 见 `pkg/vectors/AGENTS.md`，Embedding contract 见
`pkg/embeddings/AGENTS.md`。

## 模块职责

本目录把业务知识块模型适配到通用向量 repository。访问范围、用户权限、允许的 domain / kb_id 和最终
回答编排由 `internal/services/rag.py` 承担；Milvus SDK 细节留在 `pkg/vectors/backends/milvus/`。

## 修改约束

- Repository 只负责业务实体与 `VectorRecord` / `SearchHit` 的映射、允许的 scalar filter 和 collection spec。
- 不在 repository 中信任客户端范围。用户、租户、domain 和知识库授权必须由 Service 求交后再传入。
- `doc_id`、`kb_id`、`domain`、`chunk_index`、content、主键和 embedding dimension 属于持久化 contract；
  修改时评估已有 collection 重建、回填、双读/双写和回滚。
- `ensure_collection()` 只负责创建或校验，不是 schema migration；已有 collection 不兼容时不得静默重建或删数据。
- 业务 repository 不直接 import `pymilvus`，不复制 filter expression 或 retry 逻辑。
- 同一查询内避免重复 embedding 和无界 top_k；保持 similarity score、threshold 和 retrieval mode 语义一致。
- 外部 Milvus / Embedding 错误不伪装为空结果；由 Service 转换为稳定业务错误或降级策略。

## 验证重点

- 覆盖 entity/record 映射、允许与拒绝 filter、阈值、显式 query vector 和空结果。
- 优先运行相关 `tests/vector/`、`tests/vector/repositories/` 与 RAG service/API 测试；真实 Milvus 测试标记 integration。
