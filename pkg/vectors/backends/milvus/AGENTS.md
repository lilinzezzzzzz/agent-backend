# AGENTS.md

`pkg/vectors/backends/milvus/` 把通用向量 contract 翻译为 pymilvus 调用。

- `backend.py` 管理连接、collection 生命周期和读写检索，`schema.py` 管理 schema/index，`codec.py` 管理
  row、hit 和 filter 转换；不要复制这些职责。
- `ensure_collection()` 只幂等创建或校验，不执行 migration；已有 schema 不兼容时显式失败，不自动删除重建。
- collection 名、字段、dimension、metric、index、BM25、metadata 编码和 consistency 是持久化 contract。
- 仅重试有界且明确可恢复的连接错误；写入重试考虑幂等，取消传播，SDK 阻塞调用不得占用 event loop。
- 不记录 token、完整向量、正文或大批量 payload；真实 schema 和检索行为只在显式 Milvus 环境验证。
