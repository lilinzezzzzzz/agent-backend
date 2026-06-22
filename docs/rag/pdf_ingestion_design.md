# PDF 文档解析与入库技术方案

## 概述

本文档定义 RAG 知识库后续接入 PDF 文档的解析、去重、chunk、索引和质量控制方案。
它是 `docs/rag/kb_orm_admin_design.md` 的补充文档，不改变当前首版只支持 `text/plain`、
`text/markdown`、`text/html` 的结论。

核心判断：

- PDF 不应被笼统视为一种简单文本格式。
- 首个 PDF 版本只支持 native text PDF，也就是带可抽取文本层的 PDF。
- 扫描 PDF、OCR PDF、复杂多栏 PDF、加密 PDF 和表单 PDF 不进入首批自动索引路径。
- PDF 解析结果必须先生成稳定 canonical text，才能进入 normalized hash、chunk 和 embedding。

## 1. 目标与非目标

### 1.1 目标

- 为 `application/pdf` 增加可控的后续接入方案。
- 区分 native text PDF、扫描 PDF、复杂版式 PDF 和不可解析 PDF。
- 定义 PDF 解析前置校验、文本抽取、canonical text、去重和 chunk 规则。
- 明确失败错误码和质量指标，避免低质量 PDF 污染 RAG 检索结果。
- 保持现有 `RagService`、`ChunkVectorRepository` 和 KB registry contract 稳定。

### 1.2 非目标

- 不在首个 PDF 版本支持 OCR。
- 不在首个 PDF 版本支持图片、图表和公式的语义理解。
- 不在首个 PDF 版本还原复杂表格结构。
- 不在首个 PDF 版本保证多栏、脚注、页眉页脚、目录和水印全部正确。
- 不改变 `/v1/rag/answer` 响应 contract；如需暴露页码引用，应单独评估 schema 兼容性。

## 2. 支持分级

### 2.1 P0：拒绝或暂不支持

以下 PDF 类型首批应直接拒绝或标记为待人工处理：

- 无文本层的扫描 PDF。
- 需要 OCR 才能读取正文的 PDF。
- 加密 PDF 或禁止文本抽取的 PDF。
- 页数、文件大小、对象数量超过限制的 PDF。
- 主要内容为图片、图表、扫描表格或手写内容的 PDF。
- 表单 PDF、附件型 PDF、嵌入脚本或包含异常对象结构的 PDF。

建议错误码：

```text
unsupported_scanned_pdf
unsupported_encrypted_pdf
unsupported_complex_pdf
pdf_too_large
pdf_parse_failed
pdf_text_quality_low
```

### 2.2 P1：首个可实现版本

只支持 native text PDF：

- MIME type 为 `application/pdf`。
- 文件 magic header 能识别为 PDF。
- 未加密，允许文本抽取。
- 页数和文件大小在限制内。
- 大多数页面能抽取到连续文本。
- 文本抽取质量达到阈值。

P1 输出：

- `raw_content_hash`：基于 PDF 原始 bytes。
- `normalized_content_hash`：基于解析后的 canonical text。
- `canonical_text`：用于 chunk 和 embedding。
- parse stats：页数、抽取字符数、低质量页面数、跳过原因。

### 2.3 P2：后续增强

P2 可以考虑：

- OCR PDF。
- 多栏版式恢复。
- 表格结构抽取。
- 图片和图表说明抽取。
- 页码级 citation 暴露到 API response。
- parser 多版本回放和重建。

P2 进入实现前必须先有评估集，覆盖不同 PDF 版式、错误样本和引用准确率。

## 3. API 与状态边界

### 3.1 content type

PDF 接入后，`knowledge_documents.content_type` 可新增：

```text
application/pdf
```

该类型不应和首版文本类型一起默认开放，应通过配置或特性开关控制。

建议配置：

```text
RAG_PDF_INGEST_ENABLED=false
RAG_PDF_NATIVE_TEXT_ONLY=true
RAG_PDF_MAX_FILE_SIZE_MB=20
RAG_PDF_MAX_PAGES=200
RAG_PDF_MIN_TEXT_CHARS_PER_PAGE=20
RAG_PDF_MIN_TEXT_PAGE_RATIO=0.8
```

### 3.2 登记流程

PDF 文档登记沿用现有接口：

```text
POST /v1/kbs/{kb_id}/documents/register
```

Service 处理顺序：

```text
校验 content_type=application/pdf
-> 校验 PDF 开关
-> 校验文件大小、magic header、加密状态和页数
-> 计算 raw_content_hash
-> raw hash 去重
-> 写 knowledge_documents(parse_status=pending, index_status=pending)
-> 创建 parse_document task
```

不支持的 PDF 必须在登记或 parse 阶段落到稳定错误码，不创建 chunk，不写 vector。

### 3.3 状态流转

推荐状态流转：

```text
pending
-> parsing
-> parsed
-> indexing
-> indexed
```

失败分支：

```text
pending/parsing
-> failed(error_code=unsupported_scanned_pdf | pdf_parse_failed | ...)
```

质量不足但可排障的文档建议保留 document row，便于管理后台看到失败原因。

## 4. 数据模型补充

### 4.1 `knowledge_documents`

PDF 接入不要求立即扩展核心文档表，但建议补充以下可选字段或放入 parse artifact：

```text
parser_name
parser_version
parse_profile
page_count
parse_quality_score
parse_artifact_uri
```

字段语义：

- `parser_name`：例如 `pdf_native_text`。
- `parser_version`：parser 版本，用于后续重建和问题追踪。
- `parse_profile`：例如 `native_text_only`。
- `page_count`：PDF 页数。
- `parse_quality_score`：解析质量分，首版可用 0-1 浮点值。
- `parse_artifact_uri`：解析产物位置，例如 canonical text、页面文本或诊断 JSON。

如果不想扩展主表，可新增 parse artifact 表：

```text
knowledge_document_parse_artifacts
- id
- doc_id
- artifact_type
- parser_name
- parser_version
- artifact_uri
- stats_payload
- created_at
```

该表与 `knowledge_documents` 使用应用层逻辑外键，不引入数据库物理外键。

### 4.2 `knowledge_chunks`

PDF chunk 建议保留页码信息，便于后续定位引用：

```text
page_start
page_end
section_path
```

首版可以只写入 registry，不写入 vector scalar metadata。`ChunkVectorRepository` 仍只保留
`doc_id`、`kb_id`、`domain`、`chunk_index` 等检索必要字段。页码如需出现在 API response，
应通过 `RagMetadataDao` 从 registry 补齐，并单独评估 response schema 兼容性。

## 5. PDF 解析流程

### 5.1 预检

预检在 parse task 前或 task 开始时执行：

- 检查 magic header 是否为 PDF。
- 检查文件大小。
- 检查页数。
- 检查是否加密。
- 检查是否允许文本抽取。
- 抽样页面判断是否有文本层。

预检失败不进入 chunk / embedding。

### 5.2 文本抽取

首版只做 native text extraction：

- 按页抽取文本。
- 记录每页字符数。
- 识别空页和疑似扫描页。
- 保留页序。
- 不做 OCR。
- 不做复杂表格还原。

质量阈值建议：

```text
text_page_ratio = 有有效文本页数 / 总页数
min_text_chars_per_page = 单页有效文本最小字符数
```

如果 `text_page_ratio < RAG_PDF_MIN_TEXT_PAGE_RATIO`，返回 `pdf_text_quality_low` 或
`unsupported_scanned_pdf`。

### 5.3 canonical text

canonical text 是去重和 chunk 的输入，必须稳定。

规则：

- 统一编码为 UTF-8。
- 统一换行。
- 合并 PDF 抽取导致的硬换行。
- 修复行尾断词。
- 去除重复页眉、页脚和页码。
- 保留段落边界。
- 保留标题层级的可读标记。
- 对表格先降级为行文本，不在首版承诺结构化表格。

建议格式：

```text
# page 1
...

# page 2
...
```

是否保留 page marker 会影响 `normalized_content_hash`。一旦确定规则，不要随意变更；变更需要
提升 `parser_version` 并触发可控重建。

## 6. Chunk 策略

PDF chunk 应该 page-aware，而不是只按字符硬切。

建议规则：

- 优先按标题、段落和页边界切分。
- 单个 chunk 可以跨页，但必须记录 `page_start` 和 `page_end`。
- 避免把页眉页脚切入正文 chunk。
- 避免把表格行拆得无法理解。
- chunk text 使用 canonical text，不直接使用原始 PDF 抽取片段。

首版建议：

```text
target_chunk_tokens = 300-600
max_chunk_tokens = 800
chunk_overlap_tokens = 50-100
```

实际 token 参数应通过评估集确认，不要固定成不可调整常量。

## 7. 去重规则

PDF 去重使用两层 hash：

- `raw_content_hash`：PDF 原始 bytes hash。
- `normalized_content_hash`：canonical text hash。

语义：

- raw hash 命中：完全相同 PDF，默认复用已有文档。
- normalized hash 命中：不同 PDF 文件但正文一致，默认标记 duplicate 并跳过 chunk / embedding。
- parser version 改变后，normalized hash 可能改变；需要通过重建任务处理，不直接覆盖旧状态。

扫描 PDF 不应进入 normalized hash 去重，因为 OCR 结果不稳定，容易造成误判。

## 8. 质量诊断

PDF parse task 应输出诊断指标：

```text
page_count
text_page_count
empty_page_count
extracted_text_chars
text_page_ratio
header_footer_removed_count
parse_quality_score
unsupported_reason
parser_name
parser_version
```

管理后台应能展示：

- 为什么 PDF 被拒绝。
- 哪些页面没有有效文本。
- 是否疑似扫描件。
- 是否发生去重复用。
- chunk 覆盖了哪些页码。

## 9. 安全与资源控制

PDF 是高风险输入，必须做资源限制：

- 限制文件大小。
- 限制页数。
- 限制解析超时。
- 限制单页对象数或解析产物大小。
- 不执行 PDF 内嵌脚本。
- 不打开外部链接或远程资源。
- 解析任务在 worker 中隔离执行。
- 解析失败要清理临时文件。

日志不得记录完整文件内容、真实 OSS 签名 URL、临时下载 token 或敏感正文片段。

## 10. 测试策略

### 10.1 单元测试

- native text PDF 可以通过预检。
- 扫描 PDF 被拒绝。
- 加密 PDF 被拒绝。
- 超页数或超大小 PDF 被拒绝。
- canonical text 规则稳定。
- raw hash 和 normalized hash 去重路径正确。
- parser version 变化不会静默覆盖旧 hash。

### 10.2 集成测试

- PDF 登记后创建 parse task。
- parse 成功后创建 chunks。
- duplicate PDF 不创建 embedding task。
- parse 失败时 document row 保留错误码和诊断信息。
- 不支持 PDF 开关关闭时，`application/pdf` 被拒绝。

### 10.3 RAG 回归

- PDF 解析得到的 chunk 能被 `RagService.retrieve()` 检索到。
- citation 至少能回到 document title 和 source URI。
- 后续暴露页码时，citation 页码与 chunk registry 一致。
- 低质量 PDF 不进入在线 RAG 召回结果。

## 11. 分阶段实施

### Phase A：设计与离线验证

- 准备 PDF 样本集。
- 评估 native text 抽取质量。
- 确定 canonical text 规则。
- 确定错误码和质量指标。

### Phase B：受控接入 native text PDF

- 增加 `application/pdf` 白名单开关。
- 实现 PDF 预检。
- 实现 native text extraction。
- 写入 canonical text、hash、chunk 和诊断信息。
- 默认不支持 OCR。

### Phase C：质量诊断和页码引用

- 增加 page-aware chunk metadata。
- 管理后台展示 parse stats。
- 评估是否把页码暴露到 RAG citation response。

### Phase D：OCR 和复杂版式

- 只有在有评估集和成本预算后再接入。
- OCR 结果必须带 parser version 和置信度。
- 复杂表格、图片和图表应作为独立能力设计，不混入 native text PDF 首版。

## 12. 关键结论

- PDF 首版只支持 native text PDF，不支持扫描件和 OCR。
- PDF 解析必须先通过预检和质量阈值，再进入 chunk / embedding。
- canonical text 是 normalized hash 和 chunk 的事实来源，规则必须稳定并版本化。
- 页码信息应进入 registry，是否暴露到 RAG API response 需要单独做兼容性评估。
- PDF 支持应通过特性开关灰度，不应默认与 `text/plain`、`text/markdown`、`text/html` 同时开放。
