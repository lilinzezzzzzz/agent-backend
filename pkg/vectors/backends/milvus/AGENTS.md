# AGENTS.md

适用于 `pkg/vectors/backends/milvus/`。父级 contract 见 `pkg/vectors/AGENTS.md`，行为说明与使用方式保留在
本目录 `README.md`。

## 模块职责

本目录把通用 `VectorBackend`、collection spec、record、filter 和 search request 翻译为 pymilvus 调用。

## 修改约束

- `backend.py` 负责连接、collection 生命周期和读写检索；`schema.py` 负责 schema/index 映射与兼容校验；
  `codec.py` 负责 row/hit/filter 转换，不在三者间复制规则。
- Milvus 特有类型只在 backend 边界内暴露；业务 repository 不直接构造 SDK expression 或调用 `MilvusClient`。
- `ensure_collection()` 是幂等创建/校验，不是 migration。已存在 schema 不兼容时显式失败，不自动 drop/recreate。
- collection 名、字段、dimension、metric、index、BM25 function、metadata 编码和 consistency 属于持久化 contract。
- 只重试明确 recoverable 的连接错误，并限制次数/退避；写入重试必须考虑幂等，取消必须传播。
- pymilvus 同步阻塞调用不得占用 async event loop；连接和 load/release 生命周期要可重复清理。
- 不记录 token、完整向量、文档正文或大批量 payload。

## 验证重点

- 通用行为运行 `tests/vector/backends/` 与 repository 测试；schema/codec 使用 mock 覆盖无需外部服务的分支。
- 真实建表、dense/full-text/hybrid、重连和 schema mismatch 只在显式 Milvus integration 环境验证。
