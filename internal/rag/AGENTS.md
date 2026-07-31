# AGENTS.md

`internal/rag/` 负责业务实体与通用向量 contract 的映射；授权范围和回答编排属于 `RagService`，Milvus SDK
细节属于 `pkg/vectors/backends/milvus/`。

- Repository 只处理实体映射、允许的 scalar filter 和 collection spec，不直接信任客户端提供的用户、租户、
  domain 或 kb_id 范围。
- `doc_id`、`kb_id`、`domain`、`chunk_index`、主键、content 和 embedding dimension 是持久化 contract。
- `ensure_collection()` 只创建或校验，不执行 migration；已有 schema 不兼容时显式失败，不静默删表重建。
- 不在业务 repository 中 import `pymilvus`、复制 SDK filter/retry 逻辑或重复计算 embedding。
- 外部 Milvus / Embedding 错误不得伪装为空结果；由 Service 转换成稳定错误或显式降级。
