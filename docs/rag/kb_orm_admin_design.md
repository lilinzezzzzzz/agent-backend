# RAG 知识库 / 文档 / Chunk ORM 管理后台技术方案

## 概述

本文档定义 AGENT-BACKEND RAG 知识库、文档、chunk、索引任务和基础诊断能力的实现方案。
当前方案不再以外部 Agent runtime 接入为前置，也不把知识库运维设计成某个 Agent 框架的
附属能力。

新的主线是：

```text
KB / Document / Chunk 管理后台
-> 提供真实 registry、索引任务和状态治理
-> 支撑 RagService 检索、回答、引用和业务 Agent 工具调用
```

当前代码中已经存在的边界：

- 用户态 RAG API：`POST /v1/rag/answer`，由 `RagService.answer()` 执行问答。
- 内部 RAG API：`POST /v1/internal/rag/external-runs`，由 `RagService.run_external()` 执行外部 case。
- 业务 Agent：订单和支付 Agent 通过 tool 调用 `RagService.retrieve()`，不是直接访问向量库。
- 向量层：`ChunkVectorRepository` 只保存检索需要的 scalar metadata：`doc_id`、`kb_id`、
  `domain`、`chunk_index`。
- Metadata 层：`RagMetadataDao` 当前为空实现，`RagService` 会批量调用它，再用 vector hit
  metadata/payload 做 fallback。

核心目标：

- 为 RAG 建立真实 KB registry，替代只依赖配置项和 vector hit metadata 的临时方案。
- 管理知识库、文档、chunk、索引任务和基础质量状态。
- 让 `RagMetadataDao.get_chunk_metadata_map()` 能批量补齐 title、source、version 等引用字段。
- 为文档上传、解析、embedding refresh、index rebuild、vector delete 和质量评估任务打基础。
- 保持现有 `RagService`、`ChunkVectorRepository`、`/v1/rag/answer` 和业务 Agent tool contract 稳定。

## 1. 目标与非目标

### 1.1 目标

- 新增 `KnowledgeBase`、`KnowledgeDocument`、`KnowledgeChunk`、`KnowledgeIndexTask` ORM 模型。
- 新增对应 DAO、Service、DTO、Schema 和 Controller。
- 提供 KB / Document / Chunk 的基础管理 API。
- 支持分页、批量写入 chunk、状态更新、任务查询和基础统计。
- 任务表支持 parse、embedding、rebuild、delete vectors 和 quality check 等异步任务状态追踪。
- 将 `RagMetadataDao` 从空实现替换为基于真实 registry 的批量查询实现。
- 增加基于 KB registry 的 `RagScopeResolver`，让 allowed scope 可以从服务端真实数据解析，并保留
  配置 fallback。
- 与现有 `RagService`、`ChunkVectorRepository`、订单 Agent、支付 Agent 协同。

### 1.2 非目标

- 不新增独立的 RAG chat API 来替代 `POST /v1/rag/answer`。
- 不改变 `RagAnswerReqSchema` / `RagAnswerRespSchema` 的现有对外 contract。
- 不让 Agent tool 直接访问 ORM、DAO、Milvus、Redis 或 Celery。
- 不在第一阶段实现完整文件上传和文档解析 pipeline。
- 不在第一阶段实现复杂多租户 RBAC、知识库分享、审批流或跨环境发布。
- 不替代现有 `pkg/vectors/` 向量抽象。
- 不把 Celery backend 当成索引任务的业务事实来源。

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
  scope.py
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
- Service：`KbAdminService`、`KbDocumentService`、`KbChunkService`、`KbIndexTaskService`、`KbScopeService`
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
creator_type
updater_id
updater_type
```

建议约束：

- `name`：非空，长度 1-128。
- `domain`：非空，长度 1-64；与 RAG 请求中的 `requested_domain` 和 vector scalar field `domain` 对齐。
- `owner_id`：创建者或所属用户，首版用于 demo 权限和 allowed scope 解析。
- `status`：`active | disabled | deleted`。
- 普通查询默认排除 `deleted_at IS NOT NULL`。
- 业务唯一性建议为 `(owner_id, domain, name, deleted_at)` 的软删除兼容唯一约束，具体 null
  语义按目标数据库确认。

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
source_fingerprint
content_type
raw_content_hash
normalized_content_hash
dedup_status
duplicate_of_doc_id
dedup_checked_at
document_version
parse_status
index_status
error_code
error_message
created_at
updated_at
deleted_at
creator_id
creator_type
updater_id
updater_type
```

状态建议：

```text
parse_status: pending | parsing | parsed | failed
index_status: pending | indexing | indexed | failed | deleted
dedup_status: unchecked | unique | duplicate | candidate | skipped
```

建议约束：

- `kb_id` 必须引用存在且未删除的 KB，使用应用层逻辑外键，不引入数据库物理外键。
- `source_uri` 可为空；如果来自文件上传，后续记录 OSS/S3 key 或内部资源 URI。
- `content_type` 首版只允许 `text/plain`、`text/markdown`、`text/html`。
- `source_fingerprint` 是规范化来源标识，例如 normalized URL、OSS key 或外部系统 document id hash。
- `raw_content_hash` 是原始 bytes 或原始内容 hash，用于上传/登记阶段的精确去重。
- `normalized_content_hash` 是解析后 canonical text hash，用于跨格式精确去重。
- `dedup_status` 记录文档去重结论；`duplicate_of_doc_id` 使用应用层逻辑引用原始文档。
- `document_version` 用于引用和审计，默认 `v1`，更新文档内容时递增或生成新版本。
- `error_message` 只保存可排障摘要，不保存完整外部响应、密钥、token 或连接串。

建议索引：

```text
idx_docs_kb_status_id(kb_id, index_status, id)
idx_docs_kb_parse_status_id(kb_id, parse_status, id)
idx_docs_kb_raw_hash(kb_id, raw_content_hash, id)
idx_docs_kb_normalized_hash(kb_id, normalized_content_hash, id)
idx_docs_kb_source_fingerprint(kb_id, source_fingerprint, id)
idx_docs_duplicate_of(duplicate_of_doc_id, id)
```

去重约束：

- 去重范围默认限定在同一个 KB 的 active documents，不跨 owner 或跨 KB 自动复用。
- 服务端必须重新计算或校验 hash，不信任客户端传入的 hash。
- 同 KB 内 `raw_content_hash` 命中时，默认返回已有文档，不创建 parse / embedding / vector 任务。
- `normalized_content_hash` 在 parse 完成后计算；命中时标记当前文档为 duplicate 并跳过 chunk
  写入。
- 近似重复只作为 `candidate` 诊断结果，不在第一阶段自动阻断写入。
- 如需强唯一约束，必须按目标数据库确认软删除语义。PostgreSQL 可用 partial unique index；
  MySQL 需用 generated active marker 或 Service 事务内重查加唯一冲突处理。

首版支持文档类型：

| `content_type` | 常见后缀 | 解析策略 |
| --- | --- | --- |
| `text/plain` | `.txt` | 按 UTF-8 文本读取并执行换行、空白规范化 |
| `text/markdown` | `.md`, `.markdown` | 保留标题、列表和段落结构，去除不影响语义的格式噪声 |
| `text/html` | `.html`, `.htm` | 提取可见正文，过滤 script、style、导航和模板噪声 |

不在首版支持 PDF、DOCX、PPTX、XLSX、图片 OCR、音视频转写或压缩包。后续接入这些格式时，
必须先定义 parser、大小限制、超时、失败错误码和 canonical text 规则，再允许进入 chunk /
embedding 流程。
PDF 后续处理方案见 `docs/rag/pdf_ingestion_design.md`。

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
creator_type
updater_id
updater_type
```

状态建议：

```text
vector_status: pending | indexing | indexed | failed | deleted
```

关键约束：

- `KnowledgeChunk.id` 应与 `ChunkVectorRepository` 写入 vector backend 的 primary key 一致。
- `kb_id`、`doc_id`、`chunk_index` 必须同步写入 vector metadata。
- `domain` 不在 chunk 表重复存储，写 vector 时从 `knowledge_bases.domain` 派生；如果后续为性能
  冗余，必须同步说明一致性策略。
- `text_hash` 用于去重和定位 chunk 内容漂移。
- `chunk_index` 在同一 `doc_id` 内应稳定，建议对 `(doc_id, chunk_index, deleted_at)` 做软删除
  兼容唯一约束。

建议索引：

```text
idx_chunks_kb_vector_status_id(kb_id, vector_status, id)
idx_chunks_doc_chunk_index(doc_id, chunk_index)
idx_chunks_text_hash(text_hash)
```

### 3.4 `knowledge_index_tasks`

索引运维任务表。记录文档解析、embedding refresh、索引重建、向量删除和质量检查等异步
动作。

```text
id
task_id
kb_id
doc_id
task_type
status
trigger_source
trigger_user_id
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
- 管理后台查询任务状态和失败原因。
- `idempotency_key` 防止重复提交任务。
- `request_payload` / `result_payload` 只记录必要参数、统计和资源 ID，不记录 chunk 全文或 provider
  secret。

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
- list_by_owner(owner_id, status, domain, cursor, limit)
- list_active_scope_by_owner(owner_id, requested_domain, requested_kb_ids)
- insert(kb)
- update_fields(kb_id, fields)

KnowledgeDocumentDao
- get_by_id(doc_id)
- get_by_ids(doc_ids)
- get_active_by_raw_hash(kb_id, raw_content_hash)
- get_active_by_normalized_hash(kb_id, normalized_content_hash)
- get_latest_by_source_fingerprint(kb_id, source_fingerprint)
- list_by_kb(kb_id, cursor, limit, parse_status, index_status)
- count_by_kb(kb_id)
- count_by_kb_grouped_status(kb_id)
- insert(document)
- update_status(doc_id, parse_status, index_status, error_code, error_message)

KnowledgeChunkDao
- get_by_id(chunk_id)
- get_by_ids(chunk_ids)
- list_by_doc(doc_id, cursor, limit)
- list_by_ids_with_document(chunk_ids)
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
- 写路径需要应用层校验逻辑外键，不能依赖数据库物理外键或级联删除。

## 5. Service 设计

### 5.1 `KbAdminService`

职责：

- 创建、查询、更新、启用、禁用知识库。
- 校验 owner、domain、状态。
- 返回 DTO，不暴露 ORM。

建议方法：

```text
create_kb(user_id, name, domain, description) -> KbDetailDTO
get_kb_detail(user_id, kb_id) -> KbDetailDTO
list_kbs(user_id, cursor, limit, status, domain) -> KbListDTO
update_kb(user_id, kb_id, name, description) -> KbDetailDTO
enable_kb(user_id, kb_id) -> KbDetailDTO
disable_kb(user_id, kb_id) -> KbDetailDTO
```

### 5.2 `KbDocumentService`

职责：

- 登记文档 metadata。
- 执行文档精确去重，默认复用同 KB 下已存在的 active document。
- 分页查询 KB 下文档。
- 更新解析状态和索引状态。

建议方法：

```text
register_document(user_id, kb_id, title, source_uri, raw_content_hash, dedup_policy) -> KbDocumentDTO
get_document_detail(user_id, kb_id, doc_id) -> KbDocumentDTO
list_documents(user_id, kb_id, cursor, limit, parse_status, index_status) -> KbDocumentListDTO
update_document(user_id, kb_id, doc_id, title, source_uri) -> KbDocumentDTO
mark_document_status(user_id, kb_id, doc_id, parse_status, index_status, error_code, error_message) -> KbDocumentDTO
mark_document_deduplicated(user_id, kb_id, doc_id, duplicate_of_doc_id) -> KbDocumentDTO
```

去重策略：

- `dedup_policy` 默认 `reuse_existing`；命中重复时返回已有文档，`deduped=true`。
- `create_new_version` 仅在同一 `source_fingerprint` 内容变化时使用，生成新的 `document_version`。
- `force_new` 不给普通用户默认开放，避免人为制造重复 chunk 和重复 vector。
- 并发登记同一文档时，Service 必须在事务内重查或处理唯一冲突，避免重复任务。

### 5.3 `KbChunkService`

职责：

- 批量写入 chunk registry。
- 查询 chunk 列表和质量指标。
- 更新向量状态。

建议方法：

```text
batch_create_chunks(user_id, kb_id, doc_id, chunks) -> KbChunkBatchDTO
list_chunks(user_id, kb_id, doc_id, cursor, limit) -> KbChunkListDTO
summarize_chunk_quality(user_id, kb_id) -> KbChunkQualityDTO
batch_mark_vector_status(user_id, kb_id, chunk_ids, status) -> KbChunkBatchDTO
```

批量写入要求：

- 先一次性校验 KB 和 document 归属。
- 同一请求内 `chunk_index` 不得重复。
- 支持幂等重放时必须用稳定业务键或显式 idempotency key。
- 不在循环中单条 insert 或单条查询 document。

### 5.4 `KbIndexTaskService`

职责：

- 创建索引任务。
- 处理 idempotency。
- 查询任务状态。
- worker 回写任务状态。

建议方法：

```text
create_index_task(user_id, kb_id, doc_id, task_type, request_payload, idempotency_key) -> KbIndexTaskDTO
get_task_status(user_id, kb_id, task_id) -> KbIndexTaskDTO
list_tasks(user_id, kb_id, cursor, limit, status) -> KbIndexTaskListDTO
mark_task_running(task_id) -> KbIndexTaskDTO
mark_task_succeeded(task_id, result_payload) -> KbIndexTaskDTO
mark_task_failed(task_id, error_code, error_message) -> KbIndexTaskDTO
```

一致性要求：

- 创建任务时先写 `knowledge_index_tasks`，再投递 Celery。
- 投递失败时任务保持 `failed` 或可重试的 `queued`，必须有可观测错误信息。
- 外部网络 I/O 不放在数据库事务内。
- 任务执行应幂等，重复执行不能生成重复 vector 或错误覆盖成功状态。

### 5.5 `KbScopeService`

职责：

- 将用户可访问 KB 解析为 `RagService` 可使用的 allowed domain 和 allowed kb ids。
- 替换当前只从 `settings.RAG_ALLOWED_DOMAINS` / `settings.RAG_ALLOWED_KB_IDS` 读取的临时解析。
- 保留配置 fallback，避免 registry 尚未初始化时使 RAG demo 完全不可用。

建议方法：

```text
resolve_allowed_domains(user_id, requested_context) -> set[str]
resolve_allowed_kb_ids(user_id, requested_context) -> set[int]
```

接入方式：

```text
RagService
-> KbScopeService / RagScopeResolver
-> KnowledgeBaseDao.list_active_scope_by_owner()
```

规则：

- 只返回 `status = active` 且未软删除的 KB。
- `requested_domain` 和 `requested_kb_ids` 仍只是客户端期望范围，最终范围由服务端 allowed scope
  求交。
- 如果用户没有任何可用 KB，`RagService.retrieve()` 保持返回 `rag_scope_empty` 的稳定语义。

## 6. API 设计

新增用户态 KB 管理 API，挂载在 `/v1` 下，默认走 token 认证。

```text
POST /v1/kbs/create
GET  /v1/kbs
GET  /v1/kbs/{kb_id}
POST /v1/kbs/{kb_id}/update
POST /v1/kbs/{kb_id}/enable
POST /v1/kbs/{kb_id}/disable

POST /v1/kbs/{kb_id}/documents/register
GET  /v1/kbs/{kb_id}/documents
GET  /v1/kbs/{kb_id}/documents/{doc_id}
POST /v1/kbs/{kb_id}/documents/{doc_id}/update
POST /v1/kbs/{kb_id}/documents/{doc_id}/parse
POST /v1/kbs/{kb_id}/documents/{doc_id}/refresh-embedding

POST /v1/kbs/{kb_id}/documents/{doc_id}/chunks/batch-create
GET  /v1/kbs/{kb_id}/documents/{doc_id}/chunks
GET  /v1/kbs/{kb_id}/chunks/quality

POST /v1/kbs/{kb_id}/index-tasks/create
GET  /v1/kbs/{kb_id}/index-tasks
GET  /v1/kbs/{kb_id}/index-tasks/{task_id}
```

API 规则：

- GET 只读，路径使用资源名，不加 `get` / `list` 动词。
- POST 写操作必须把动作放在路径末尾，例如 `create`、`update`、`disable`、`batch-create`。
- Controller 使用 `BaseResponse[T]` / `BaseListResponse[T]`。
- Controller 不直接访问 DAO、Redis、Milvus、Celery。
- 请求 schema 做字段长度、枚举和分页校验。
- 资源归属、状态流转、幂等和任务投递在 Service。
- 列表接口必须分页。

文档登记 API 去重 contract：

```text
dedup_policy: reuse_existing | create_new_version | force_new
```

默认使用 `reuse_existing`。响应 schema 应包含：

```text
deduped
dedup_status
duplicate_of_doc_id
reused_doc_id
skipped_tasks
```

字段语义：

- `deduped=true` 表示本次登记未创建新的待解析/待索引文档。
- `duplicate_of_doc_id` 指向当前重复文档的原始文档。
- `reused_doc_id` 表示客户端后续应使用的文档 ID。
- `skipped_tasks` 记录因去重跳过的 `parse_document`、`refresh_embedding` 等任务。

现有 RAG API 保持不变：

```text
POST /v1/rag/answer
POST /v1/internal/rag/external-runs
```

## 7. 与 RAG / Vector 的关系

### 7.1 `RagMetadataDao`

实现真实 registry 后，`RagMetadataDao.get_chunk_metadata_map()` 应批量查询
`knowledge_chunks` 和 `knowledge_documents`：

```text
chunk_ids
-> KnowledgeChunkDao.list_by_ids_with_document()
-> KnowledgeDocument rows
-> RagChunkMetadata map
```

要求：

- 批量查询，不按 chunk 循环查文档。
- vector hit metadata 仍作为 fallback。
- 不改变 `RagService` 对外 DTO contract。
- 缺失 registry row 时返回空 metadata，让 `RagService` 继续使用 vector hit metadata。
- 如果 chunk 已软删除或 vector_status 非 `indexed`，默认不补齐 metadata；是否仍允许 fallback 由
  `RagService` 当前兼容策略决定。

### 7.2 `RagService`

`RagService` 继续作为 RAG 用例入口：

- `answer()` 负责检索、生成回答、校验 citation。
- `retrieve()` 负责 scope 求交、调用 vector repository、后处理和 evidence 构造。
- `run_external()` 负责内部 case 运行数据组装。

新增 KB registry 后，`RagService` 的变化应限制在依赖注入和内部实现：

- 注入 DB-backed `RagScopeResolver` / `KbScopeService`。
- 注入真实 `RagMetadataDao`。
- 保持 `RagAnswerDTO`、`RagRetrievalDTO`、`RagExternalRunDTO` 字段语义稳定。
- 保持 `rag_scope_empty`、`rag_no_evidence`、`rag_invalid_citation` 等稳定错误原因。

### 7.3 `ChunkVectorRepository`

`ChunkVectorRepository` 继续只负责 vector 层：

- collection spec
- scalar filter
- vector / hybrid search
- vector upsert
- vector delete

它不承担：

- KB 权限。
- 文档状态。
- 任务状态。
- 管理后台分页。
- 引用字段补齐。

`KnowledgeChunk.id` 与 vector primary key 同步，由 Service / indexing workflow 保证。

## 8. 索引与写入流程

### 8.1 文档登记

```text
POST /v1/kbs/{kb_id}/documents/register
-> KbDocumentService.register_document()
-> 校验 content_type 是否属于首版允许列表
-> 计算或校验 raw_content_hash
-> 查同 KB active document 是否已有相同 raw_content_hash
-> 未命中时写 knowledge_documents(parse_status=pending, index_status=pending)
```

第一阶段只登记 metadata，不要求同时完成文件上传、解析和 embedding。

类型校验：

- `content_type` 必须是 `text/plain`、`text/markdown`、`text/html` 之一。
- 不支持的类型应在登记阶段返回稳定业务错误，不创建 document row 或异步任务。
- 文件后缀只能作为辅助判断，最终以服务端识别或校验后的 MIME type 为准。

登记阶段精确去重：

- `raw_content_hash` 命中时，默认返回已有 `doc_id`，不创建 parse / embedding / vector 任务。
- `source_fingerprint` 相同且内容 hash 变化时，视为同来源新版本，生成新的 `document_version`。
- `source_fingerprint` 不同但 hash 相同，视为重复内容，默认复用已有文档。
- 服务端必须在事务内处理并发登记，不能只依赖写前查询。

解析后精确去重：

```text
parse_document task
-> 生成 canonical text
-> 计算 normalized_content_hash
-> 查同 KB active document 是否已有相同 normalized_content_hash
-> 命中时标记 dedup_status=duplicate，duplicate_of_doc_id=<existing_doc_id>
-> 跳过 chunk 写入、embedding refresh 和 vector upsert
```

canonical text 规则：

- 统一换行、空白和编码。
- 保留段落边界，避免过度归一导致误判。
- 页眉、页脚和页码清理策略必须稳定，方便重复运行得到同一 hash。

近似去重：

- Phase 3 可通过 SimHash、MinHash 或 shingling 产生 near duplicate 候选。
- 近似重复只标记 `dedup_status=candidate` 或写质量诊断结果，不自动跳过索引。
- 管理后台确认后再执行合并、禁用或删除任务。

### 8.2 chunk 批量写入

```text
POST /v1/kbs/{kb_id}/documents/{doc_id}/chunks/batch-create
-> KbChunkService.batch_create_chunks()
-> 批量写 knowledge_chunks(vector_status=pending)
-> 更新 document parse_status=parsed
```

要求：

- 该接口面向受控后台或后续解析任务，不给 LLM tool 直接调用。
- 同一 document 的 chunk 顺序必须稳定。
- chunk 文本进入数据库前要明确最大长度和脱敏策略。

### 8.3 embedding refresh

```text
POST /v1/kbs/{kb_id}/documents/{doc_id}/refresh-embedding
-> KbIndexTaskService.create_index_task(task_type=refresh_embedding)
-> Celery worker 读取 pending chunks
-> 调用 embedder 和 ChunkVectorRepository
-> 批量更新 chunk vector_status
-> 更新 document index_status
```

失败处理：

- 单个 chunk 失败不能伪装为整批成功。
- worker 应记录失败 chunk 数量、错误码和可重试状态。
- 重试必须避免重复插入不可覆盖的 vector 记录；优先使用稳定 chunk id upsert。

### 8.4 index rebuild / vector delete

`rebuild_index` 和 `delete_vectors` 必须作为显式任务：

- 先写 `knowledge_index_tasks`。
- 再执行 vector 写删。
- 成功后回写 task、document、chunk 状态。
- 失败时保留旧 registry 和 vector fallback，不让在线 RAG 直接中断。

## 9. 迁移与部署

首版新增表采用 additive migration：

1. 部署 DDL：新增表、索引和约束。
2. 部署 ORM / DAO / Service。
3. 部署 KB 管理 API。
4. 启用 raw hash / normalized hash 去重查询，先不强依赖唯一约束阻断写入。
5. 将 `RagMetadataDao` 从空实现替换为批量查询实现。
6. 将 `RagService` 的 scope resolver 从纯配置升级为 registry 优先、配置 fallback。
7. 再接入解析、embedding refresh 和 rebuild 任务。

回滚策略：

- 如果只新增表且没有写入关键路径，回滚代码即可，表可以暂时保留。
- 如果已开始写入 registry，回滚前保留现有 RAG/vector fallback。
- 不在同一次部署中删除旧配置项或改变 vector metadata contract。
- 不在同一次部署中把 RAG allowed scope 从配置强切到数据库；需要灰度开关或明确初始化
  数据。
- 不在首个版本强制清理历史重复文档；如已有数据，先补 hash 字段，再分批生成候选
  报告。

数据安全：

- 不运行未经确认的真实环境 backfill。
- 大表 backfill 必须分批、可恢复、可幂等。
- 索引创建需要评估锁表和运行时间。
- 不引入数据库物理外键，逻辑引用由 Service 校验和修复任务治理。
- 去重唯一约束必须按目标数据库验证软删除语义和并发冲突处理后再启用。

## 10. 测试策略

### 10.1 DAO 测试

- 单条查询、批量查询、空结果。
- 分页和稳定排序。
- 软删除过滤。
- 状态过滤。
- 按 `raw_content_hash` / `normalized_content_hash` 查询同 KB active 文档。
- 按 `source_fingerprint` 查询同来源最新版本。
- 批量 chunk 写入。
- `RagMetadataDao.get_chunk_metadata_map()` 批量补齐 metadata，不产生 N+1。

### 10.2 Service 测试

- 创建 KB。
- 非 owner 访问拒绝。
- 文档登记和状态更新。
- raw hash 命中时默认复用已有文档，并跳过 parse / embedding / vector 任务。
- normalized hash 命中时标记 duplicate，并跳过 chunk 写入。
- 同一 source fingerprint 内容变化时生成新版本，不误判为重复内容。
- `text/plain`、`text/markdown`、`text/html` 可以登记；其他 content type 被拒绝且不创建任务。
- 并发登记同一文档时只产生一个 active 原始文档或返回同一个 reused doc。
- chunk 批量写入不产生 N+1。
- idempotency key 重放返回同一任务。
- 不同任务复用 idempotency key 被拒绝。
- DB-backed scope resolver 只返回 active KB，并与 requested scope 求交。

### 10.3 API 测试

- 请求字段校验。
- 分页参数边界。
- `BaseResponse[T]` / `BaseListResponse[T]` 响应结构。
- 权限失败和资源不存在。
- 文档登记响应包含 `deduped`、`dedup_status`、`duplicate_of_doc_id` 和 `skipped_tasks`。
- POST 写接口路径符合 `.../<action>` 约定。

### 10.4 RAG 回归

- `RagMetadataDao` 能通过真实 registry 补齐 title、source、version。
- vector metadata fallback 仍可用。
- `RagService.retrieve()` scope 求交语义不变。
- `RagService.answer()` citation 校验语义不变。
- `/v1/rag/answer` response contract 不变。
- `/v1/internal/rag/external-runs` 不暴露 `effective_domains` / `effective_kb_ids`。

### 10.5 任务测试

- 创建任务后投递成功。
- 投递失败有稳定状态和错误码。
- worker running / succeeded / failed 状态流转。
- refresh embedding 幂等重试不会重复生成不一致 vector。
- delete vectors 失败不会误标 registry 为 deleted。

## 11. 分阶段实施建议

### Phase 1：Registry 与 RAG Metadata

- 新增四张表和基础 DAO。
- 实现 KB / document / chunk 基础 Service。
- 实现 raw hash 和 normalized hash 的确定性去重。
- 实现首版 content type 白名单：`text/plain`、`text/markdown`、`text/html`。
- 实现 `RagMetadataDao` 真实批量查询。
- 增加 KB 管理最小 API。
- 保持 `RagService` 对外 contract 不变。

验收标准：

- RAG 引用字段可以从 registry 补齐。
- registry 缺失时仍能使用 vector metadata fallback。
- KB / document / chunk 列表均分页。
- 不支持的文档类型不会进入 parse / embedding / vector 任务。
- 重复文档默认复用已有文档，不重复创建索引任务。

### Phase 2：Scope Resolver 与索引任务

- 实现 `KbScopeService`。
- `RagService` 优先使用 DB-backed allowed scope，配置作为 fallback。
- 实现 `KnowledgeIndexTask` 创建和查询。
- 增加 embedding refresh / rebuild / delete vectors 任务骨架。

验收标准：

- 用户只能检索自己 active KB 的数据。
- 重复提交相同 idempotency key 不重复创建任务。
- 任务状态可查询，失败原因可排障。

### Phase 3：解析、质量诊断和评估

- 接入文件上传和解析 pipeline。
- 补充 chunk 质量统计和索引诊断。
- 增加 near duplicate 候选检测，只做诊断和人工确认，不自动阻断写入。
- 如需支持 PDF、DOCX、OCR 或音视频转写，先补 parser contract 和 canonical text 规则；PDF
  细节见 `docs/rag/pdf_ingestion_design.md`。
- 接入 RAG 回归评估集和质量检查任务。
- 按需要优化 cursor pagination、索引和批量写入性能。

验收标准：

- 新文档可以从登记到 indexed 全链路完成。
- 质量检查能发现空 chunk、重复 chunk、未索引 chunk 和引用缺失。
- 近似重复文档能被标记为 candidate，确认前不影响在线 RAG。
- RAG 回归用例覆盖 no evidence、空 scope、invalid citation 和正常回答。

## 12. 关键结论

- RAG 能力应独立建设，不依赖任何外部 Agent runtime 作为前置。
- 完整 KB / Document / Chunk ORM 管理后台值得实现，优先服务 `RagService` 的检索、引用和
  scope 治理。
- 现有 `/v1/rag/answer` 和 `/v1/internal/rag/external-runs` contract 不应因管理后台新增而破坏。
- 业务 Agent 可以继续通过 `RagService.retrieve()` 使用知识检索，不能绕过 Service 直接访问 DAO 或
  vector backend。
- `RagMetadataDao` 是 registry 接入在线 RAG 的最小切入点，应优先实现批量查询和 fallback。
- 文档去重应先做 raw / normalized hash 的确定性去重，near duplicate 只作为质量诊断候选。
- 任务表 `knowledge_index_tasks` 是异步运维动作的业务事实来源，不应只依赖 Celery backend。
