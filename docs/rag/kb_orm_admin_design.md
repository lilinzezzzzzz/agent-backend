# 知识库 / 文档 / Chunk ORM 管理后台技术方案

## 概述

本文档定义 AGENT-BACKEND 后续完整实现知识库、文档、chunk 和索引任务管理后台的技术方案。
它是 AgentScope 2.0 Agent Service 中知识库运维 tool / skill 的后续演进文档，不是 AgentScope
Service 首版接入的前置条件。

关系说明：

```text
docs/agent/agentscope_service_integration.md
-> 首版通过 AgentScope 官方 Agent Service 接入，KB 运维能力先作为 tool / skill 规划

docs/rag/kb_orm_admin_design.md
-> 后续把 KB registry 替换为真实 ORM / DAO / Service / API
```

核心目标：

- 为 RAG 和 AgentScope Agent Service 中的 KB 运维能力提供真实 KB registry。
- 管理知识库、文档、chunk、索引任务和基础质量状态。
- 让 `RagMetadataDao` 可以批量查询真实 metadata，减少对 vector hit metadata 的依赖。
- 为后续文档上传、解析、embedding refresh、index rebuild 和质量评估任务打基础。

## 1. 目标与非目标

### 1.1 目标

- 新增 `KnowledgeBase`、`KnowledgeDocument`、`KnowledgeChunk`、`KnowledgeIndexTask` ORM 模型。
- 新增对应 DAO、Service、DTO、Schema 和 Controller。
- 提供 KB / Document / Chunk 的基础管理 API。
- 支持分页、批量写入 chunk、状态更新和基础统计。
- 任务表支持 parse / embedding / rebuild 等异步任务状态追踪。
- 与现有 `RagService`、`ChunkVectorRepository`、`RagMetadataDao` 协同。

### 1.2 非目标

- 不在第一阶段实现完整文件上传和文档解析 pipeline。
- 不在第一阶段实现真实 Milvus upsert / delete workflow。
- 不在第一阶段实现复杂多租户 RBAC、知识库分享、审批流或跨环境发布。
- 不让 AgentScope tool 直接访问 ORM/DAO。
- 不替代现有 `pkg/vectors/` 向量抽象。

## 2. 推荐目录

```text
internal/models/knowledge_base.py
internal/dao/knowledge_base.py
internal/services/kb/
  __init__.py
  admin.py
  documents.py
  chunks.py
  index_tasks.py
  diagnostics.py
internal/services/dto/knowledge_base.py
internal/schemas/knowledge_base.py
internal/controllers/api/knowledge_base.py
ddl/knowledge_base.sql
tests/api/test_knowledge_base.py
tests/services/test_knowledge_base.py
tests/dao/test_knowledge_base.py
```

命名保持项目分层规则：

- ORM：`KnowledgeBase`、`KnowledgeDocument`、`KnowledgeChunk`、`KnowledgeIndexTask`
- DAO：`KnowledgeBaseDao`、`KnowledgeDocumentDao`、`KnowledgeChunkDao`、`KnowledgeIndexTaskDao`
- Service：`KbAdminService`、`KbDocumentService`、`KbChunkService`、`KbIndexTaskService`
- DTO：放在 `internal/services/dto/knowledge_base.py`
- Schema：放在 `internal/schemas/knowledge_base.py`

## 3. 数据模型

### 3.1 `knowledge_bases`

知识库 registry。记录知识库本身，不记录大文本。

```text
id
name
domain
description
owner_id
status
created_at
updated_at
deleted_at
creator_id
updater_id
```

建议约束：

- `name`：非空，长度 1-128。
- `domain`：非空，长度 1-64；与 `RAG_ALLOWED_DOMAINS` 语义对齐。
- `owner_id`：创建者或所属用户，首版用于 demo 权限。
- `status`：`active | disabled | deleted`。
- 普通查询默认排除 `deleted_at IS NOT NULL`。

建议索引：

```text
idx_kbs_owner_status_id(owner_id, status, id)
idx_kbs_domain_status_id(domain, status, id)
```

### 3.2 `knowledge_documents`

文档 registry。记录文档来源、版本和处理状态。

```text
id
kb_id
title
source_uri
content_type
content_hash
document_version
parse_status
index_status
error_message
created_at
updated_at
deleted_at
creator_id
updater_id
```

状态建议：

```text
parse_status: pending | parsing | parsed | failed
index_status: pending | indexing | indexed | failed
```

建议约束：

- `kb_id` 必须引用存在且未删除的 KB。
- `source_uri` 可为空；如果来自文件上传，后续记录 OSS/S3 key。
- `content_hash` 用于识别重复文档版本。
- `document_version` 用于引用和审计，默认 `v1`。

建议索引：

```text
idx_docs_kb_status_id(kb_id, index_status, id)
idx_docs_kb_parse_status_id(kb_id, parse_status, id)
idx_docs_content_hash(content_hash)
```

### 3.3 `knowledge_chunks`

chunk registry。记录 chunk 文本、顺序和向量状态。

```text
id
kb_id
doc_id
chunk_index
text
text_hash
token_count
vector_status
created_at
updated_at
deleted_at
creator_id
updater_id
```

状态建议：

```text
vector_status: pending | indexing | indexed | failed
```

关键约束：

- `id` 应与 `ChunkVectorRepository` 写入 Milvus 的 vector primary key 一致。
- `kb_id`、`doc_id`、`chunk_index` 必须同步写入 vector metadata。
- `text_hash` 用于去重和定位 chunk 内容漂移。
- `chunk_index` 在同一 `doc_id` 内应稳定。

建议索引：

```text
idx_chunks_kb_vector_status_id(kb_id, vector_status, id)
idx_chunks_doc_chunk_index(doc_id, chunk_index)
idx_chunks_text_hash(text_hash)
```

### 3.4 `knowledge_index_tasks`

索引运维任务表。记录文档解析、embedding refresh、索引重建等异步动作。

```text
id
task_id
kb_id
doc_id
task_type
status
trigger_source
trigger_user_id
pending_action_id
idempotency_key
request_payload
result_payload
error_code
error_message
started_at
finished_at
created_at
updated_at
```

任务类型：

```text
parse_document
refresh_embedding
rebuild_index
delete_vectors
quality_check
```

任务状态：

```text
queued
running
succeeded
failed
cancelled
```

用途：

- API 确认后先写任务表，再投递 Celery。
- worker 执行时更新状态。
- Copilot 查询任务状态和失败原因。
- idempotency key 防止重复提交任务。

建议索引：

```text
idx_index_tasks_task_id(task_id)
idx_index_tasks_kb_status_id(kb_id, status, id)
idx_index_tasks_doc_status_id(doc_id, status, id)
idx_index_tasks_user_idempotency(trigger_user_id, idempotency_key)
```

## 4. DAO 设计

DAO 层只封装数据访问，不做业务权限和状态流转。

建议方法：

```text
KnowledgeBaseDao
- get_by_id(kb_id)
- get_by_ids(kb_ids)
- list_by_owner(owner_id, status, cursor, limit)
- insert(kb)
- update_fields(kb_id, fields)

KnowledgeDocumentDao
- get_by_id(doc_id)
- get_by_ids(doc_ids)
- list_by_kb(kb_id, cursor, limit, parse_status, index_status)
- count_by_kb(kb_id)
- count_by_kb_grouped_status(kb_id)
- insert(document)
- update_status(doc_id, parse_status, index_status, error_message)

KnowledgeChunkDao
- get_by_id(chunk_id)
- get_by_ids(chunk_ids)
- list_by_doc(doc_id, cursor, limit)
- count_by_kb_grouped_status(kb_id)
- batch_insert(chunks)
- batch_update_vector_status(chunk_ids, status)

KnowledgeIndexTaskDao
- get_by_task_id(task_id)
- get_by_user_idempotency_key(user_id, idempotency_key)
- list_by_kb(kb_id, cursor, limit, status)
- insert(task)
- update_status(task_id, status, result_payload, error_code, error_message)
```

硬约束：

- 列表查询必须分页。
- 批量查询必须一次完成，避免 N+1。
- 不在 DAO 中吞掉数据库异常。
- 不在 DAO 中创建 engine/session。
- 需要稳定排序时显式使用 `id` 或 `created_at + id`。

## 5. Service 设计

### 5.1 `KbAdminService`

职责：

- 创建、查询、更新、禁用知识库。
- 校验 owner、domain、状态。
- 返回 DTO，不暴露 ORM。

建议方法：

```text
create_kb(user_id, name, domain, description) -> KbDetailDTO
get_kb_detail(user_id, kb_id) -> KbDetailDTO
list_kbs(user_id, page, limit, status, domain) -> KbListDTO
update_kb(user_id, kb_id, name, description, status) -> KbDetailDTO
```

### 5.2 `KbDocumentService`

职责：

- 登记文档 metadata。
- 分页查询 KB 下文档。
- 更新解析状态和索引状态。

建议方法：

```text
register_document(user_id, kb_id, title, source_uri, content_hash, document_version) -> KbDocumentDTO
list_documents(user_id, kb_id, page, limit, parse_status, index_status) -> KbDocumentListDTO
update_document_status(user_id, doc_id, parse_status, index_status, error_message) -> KbDocumentDTO
```

### 5.3 `KbChunkService`

职责：

- 批量写入 chunk registry。
- 查询 chunk 列表和质量指标。
- 更新向量状态。

建议方法：

```text
batch_create_chunks(user_id, kb_id, doc_id, chunks) -> KbChunkBatchDTO
list_chunks(user_id, kb_id, doc_id, page, limit) -> KbChunkListDTO
summarize_chunk_quality(user_id, kb_id) -> KbChunkQualityDTO
batch_mark_vector_status(user_id, chunk_ids, status) -> KbChunkBatchDTO
```

### 5.4 `KbIndexTaskService`

职责：

- 创建索引任务。
- 处理 idempotency。
- 查询任务状态。
- worker 回写任务状态。

建议方法：

```text
create_index_task(user_id, kb_id, doc_id, task_type, request_payload, idempotency_key) -> KbIndexTaskDTO
get_task_status(user_id, task_id) -> KbIndexTaskDTO
mark_task_running(task_id) -> KbIndexTaskDTO
mark_task_succeeded(task_id, result_payload) -> KbIndexTaskDTO
mark_task_failed(task_id, error_code, error_message) -> KbIndexTaskDTO
```

## 6. API 设计

建议新增用户态 API：

```text
POST /v1/kbs
GET  /v1/kbs
GET  /v1/kbs/{kb_id}
PATCH /v1/kbs/{kb_id}

POST /v1/kbs/{kb_id}/documents
GET  /v1/kbs/{kb_id}/documents
GET  /v1/kbs/{kb_id}/documents/{doc_id}
PATCH /v1/kbs/{kb_id}/documents/{doc_id}

POST /v1/kbs/{kb_id}/documents/{doc_id}/chunks:batch-create
GET  /v1/kbs/{kb_id}/documents/{doc_id}/chunks

GET  /v1/kbs/{kb_id}/index-tasks
GET  /v1/kbs/{kb_id}/index-tasks/{task_id}
```

API 规则：

- Controller 使用 `BaseResponse[T]` / `BaseListResponse[T]`。
- Controller 不直接访问 DAO、Redis、Milvus、Celery。
- 请求 schema 做字段长度、枚举和分页校验。
- 资源归属、状态流转和幂等在 Service。
- 列表接口必须分页。

## 7. 与 RAG / Vector 的关系

### 7.1 `RagMetadataDao`

实现真实 registry 后，`RagMetadataDao.get_chunk_metadata_map()` 应批量查询
`knowledge_chunks` 和 `knowledge_documents`：

```text
chunk_ids -> KnowledgeChunk rows
doc_ids -> KnowledgeDocument rows
-> RagChunkMetadata map
```

要求：

- 批量查询，不按 chunk 循环查文档。
- vector hit metadata 仍作为 fallback。
- 不改变 `RagService` 对外 DTO contract。

### 7.2 `ChunkVectorRepository`

`ChunkVectorRepository` 继续只负责 vector 层：

- collection spec
- scalar filter
- vector/hybrid search
- vector delete

它不承担：

- KB 权限。
- 文档状态。
- 任务状态。
- 管理后台分页。

`KnowledgeChunk.id` 与 vector primary key 同步，由 Service / indexing workflow 保证。

## 8. 迁移与部署

首版新增表采用 additive migration：

1. 部署 DDL：新增表、索引和约束。
2. 部署 ORM / DAO / Service。
3. 部署管理后台 API。
4. 将 AgentScope Copilot 的 `KbMockRegistry` 替换为真实 Service。
5. 将 `RagMetadataDao` 从空实现替换为批量查询实现。

回滚策略：

- 如果只新增表且没有写入关键路径，回滚代码即可，表可以暂时保留。
- 如果已开始写入 registry，回滚前保留现有 RAG/vector fallback。
- 不在同一次部署中删除旧字段或改变 vector metadata contract。

数据安全：

- 不运行未经确认的真实环境 backfill。
- 大表 backfill 必须分批、可恢复、可幂等。
- 索引创建需要评估锁表和运行时间。

## 9. 测试策略

### 9.1 DAO 测试

- 单条查询、批量查询、空结果。
- 分页和稳定排序。
- 软删除过滤。
- 状态过滤。
- 批量 chunk 写入。

### 9.2 Service 测试

- 创建 KB。
- 非 owner 访问拒绝。
- 文档登记和状态更新。
- chunk 批量写入不产生 N+1。
- idempotency key 重放返回同一任务。
- 不同任务复用 idempotency key 被拒绝。

### 9.3 API 测试

- 请求字段校验。
- 分页参数边界。
- `BaseResponse[T]` / `BaseListResponse[T]` 响应结构。
- 权限失败和资源不存在。

### 9.4 RAG 回归

- `RagMetadataDao` 能通过真实 registry 补齐 title/source/version。
- vector metadata fallback 仍可用。
- `RagService.retrieve()` contract 不变。

## 10. 与 AgentScope Copilot 的切换点

从 mock registry 切到真实 registry 时，只替换 Service 依赖：

```text
KbMockRegistry
-> KbAdminService / KbDocumentService / KbChunkService / KbIndexTaskService
```

AgentScope 目录不应变：

- `builder.py` 仍只注入 Service。
- `tools.py` 仍只调用 Service。
- `permissions.py` 仍只做 allow / ask / deny 决策。
- `stream.py` 仍只做 event mapping。

这样可以保证 AgentScope demo 与后端管理后台解耦演进。

## 11. 关键结论

- 完整 KB / Document / Chunk ORM 管理后台值得实现，但不应该阻塞 AgentScope Copilot 首版。
- 首版 Copilot 使用 mock registry 展示 AgentScope 2.0 runtime 接入。
- ORM 管理后台实现后，通过 Service 替换 mock registry。
- `RagService` 和 `ChunkVectorRepository` contract 不应因管理后台新增而破坏。
- 任务表 `knowledge_index_tasks` 是异步运维动作的业务事实来源，不应只依赖 Celery backend。
