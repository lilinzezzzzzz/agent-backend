# Agent RAG 接入实现方案

本文档用于后续在当前 Agent 中接入 RAG 时做实现、审计和回归检查。它固定本仓库首版 RAG 的边界：不引入 `tenant_id`，`kb_id` 只作为普通 metadata filter，不作为 `ScopeValue` 或强隔离 scope。

## 1. 目标

在当前 `/v1/agent/*` 能力上接入普通生产 RAG：

```text
Hybrid Search
+ Rerank
+ Metadata Filter
+ Citation
+ Evaluation Data Export
+ Optional Lightweight ReAct
```

首版目标：

- 替换订单、支付 Agent 中的 mock 知识检索工具。
- 提供稳定的 RAG Service，供 Agent tool 和独立 RAG API 复用。
- 提供内部评测数据接口，只返回 RAG 运行数据，方便外部评测系统自行评分。
- 记录必要 trace，用于问题复盘、质量审计和回归分析。

首版不做：

- tenant / multi-tenant 隔离。
- Graph RAG。
- Heavy Agentic RAG。
- 任意外部网页搜索。
- 让模型自由决定权限、filter 或知识库范围。

## 2. 关键决策

### 2.1 `kb_id` 是普通 filter

`kb_id` 表示 knowledge base id，用于区分订单帮助中心、支付帮助中心、客服 SOP 等知识集合。

在本项目首版中：

```text
kb_id != ScopeValue
kb_id != tenant_id
kb_id 是普通 metadata filter
```

推荐检索 filter：

```text
domain = "order" | "payment" | "general"
kb_id in [...]
doc_id = ...  # 可选
```

`BaseVectorRepository.scope_value` 只适合单一强隔离字段，例如 `org_id`、`user_id`，并且只表达 `EQ` 过滤。RAG 首版可能需要多个知识库同时召回，例如 `kb_id in [1, 2, 3]`，因此不要用 `scope_value` 表达 `kb_id`。

### 2.2 不考虑 `tenant_id`

当前 Agent RAG 不设计 tenant 维度。用户态接口仍然传入 `user_id`，但首版只用于：

- audit / trace。
- 未来用户私有知识或权限扩展。
- 复用线上和评测同一条调用链。

公共业务知识的可见性由服务端知识库 registry 和 metadata filter 共同控制：

```text
domain in allowed_domains
kb_id in allowed_kb_ids
```

首版不在 vector metadata 中增加 `status` 字段。发布态由服务端知识库 / 文档 registry 控制：只有可发布、可见的 chunk 才能写入生产 RAG collection；下线、撤回或禁用文档必须通过任务删除对应 vector records 或重建索引。

### 2.3 Requested Scope vs Allowed Scope

用户态 Controller 只接收客户端的期望检索范围，不能把客户端传入的 `domain` / `kb_ids` 直接作为最终权限边界。

请求字段语义：

| 字段 | 语义 |
| --- | --- |
| `requested_domain` | 客户端期望检索的业务域，可为空 |
| `requested_kb_ids` | 客户端期望检索的知识库 ID 列表，可为空 |

`RagService` 必须从服务端配置或 DB registry 解析当前调用方可用范围：

```text
allowed_domains = resolve_allowed_domains(user_id, requested_context)
allowed_kb_ids = resolve_allowed_kb_ids(user_id, requested_context)
```

最终检索范围只能由服务端求交集得到：

```text
requested_domain_set = {requested_domain} if requested_domain else allowed_domains
requested_kb_id_set = set(requested_kb_ids) if requested_kb_ids else allowed_kb_ids

effective_domains = allowed_domains ∩ requested_domain_set
effective_kb_ids = allowed_kb_ids ∩ requested_kb_id_set

final_vector_filter =
  domain in effective_domains
  AND kb_id in effective_kb_ids
```

如果请求未传 `requested_domain` / `requested_kb_ids`，则默认使用服务端允许范围。空交集处理：

- 用户态问答：按“无可用知识范围 / 无证据”拒答，不向用户暴露具体知识库是否存在。
- 内部评测：返回稳定错误或评测失败原因，并记录 `case_id`、`subject_user_id` 和请求范围。

内部评测接口也必须通过 `subject_user_id` 解析 allowed scope。若后续需要特权评测范围，必须显式增加内部专用字段并写审计记录，不能复用用户态请求字段绕过范围解析。

### 2.4 业务 chunk repository 放在 `internal`

通用向量能力仍在 `pkg/vectors`：

- backend contract。
- Milvus backend。
- embedder。
- post retrieval。
- context assembly。
- `BaseVectorRepository`。

具体业务 chunk repository 放在：

```text
internal/vectors/chunks.py
```

原因：chunk schema、`doc_id`、`kb_id`、知识库 filter、业务错误提示都属于业务侧知识检索，不应污染 `pkg` 通用基础包。

## 3. 推荐模块边界

后续实现按以下文件组织：

```text
internal/controllers/api/rag.py
-> 用户态 RAG API，只处理 request schema、用户上下文和 response mapping

internal/controllers/internal/rag.py
-> 内部评测 API，走 /v1/internal 签名认证

internal/schemas/rag.py
-> RAG answer、retrieve、evaluation data 的 request / response schema

internal/services/rag.py
-> RAG use case 编排：filter、检索、rerank、context、生成、citation、trace

internal/services/dto/rag.py
-> Service 返回给 Controller 的 DTO

internal/vectors/chunks.py
-> 业务 chunk vector repository

internal/dao/rag.py
-> 知识库、文档、chunk、trace 记录的 ORM 访问

internal/cache/rag.py
-> query cache、短期 run state

internal/tasks/rag.py
-> 文档解析、chunk、embedding、索引重建任务
```

Controller 禁止直接访问向量库、拼 prompt 或调用 Redis。Agent tool 也不直接访问数据库或 Redis，复杂校验和编排进入 Service。

## 4. Service / Repository 生命周期

`RagService` 必须兼容仓库现有 Service DI 模式：同步 `new_xxx_service()` 懒加载单例。`ChunkVectorRepository` 的创建是异步过程，因为 `new_chunk_repository()` 会调用 `ensure_collection()`；因此不能在同步 Service factory 里直接 await，也不能在每次请求里重新创建 repository。

推荐实现：`RagService` 保持同步构造，内部对 repository 做一次性 async lazy init。

```python
class RagService:
    def __init__(
        self,
        *,
        llm_client: AgentLLMClient,
        embedder: Embedder,
        repository: ChunkVectorRepository | None = None,
        repository_factory: RagRepositoryFactory | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._embedder = embedder
        self._repository = repository
        self._repository_factory = repository_factory or new_chunk_repository
        self._repository_lock = anyio.Lock()

    async def _get_repository(self) -> ChunkVectorRepository:
        if self._repository is not None:
            return self._repository

        async with self._repository_lock:
            if self._repository is None:
                self._repository = await self._repository_factory(
                    embedder=self._embedder,
                )
            return self._repository
```

同步 factory 仍按现有模式：

```python
_rag_service: RagService | None = None


def new_rag_service() -> RagService:
    global _rag_service
    if _rag_service is None:
        _rag_service = RagService(
            llm_client=new_default_llm_client(),
            embedder=new_default_embedder(),
        )
    return _rag_service
```

实现约束：

- `new_rag_service()` 不执行 async I/O。
- `new_rag_service()` 不调用 `new_chunk_repository()`。
- `new_chunk_repository()` 只在首次真实检索时执行一次。
- repository 初始化必须加 async lock，避免并发首次请求重复创建 backend、embedder 或 collection。
- 后续请求复用同一个 repository / backend / embedder。
- 单元测试通过构造函数注入 fake repository 或 fake repository factory，不依赖真实 Milvus 和 embedding 服务。
- 不在 FastAPI Controller、Agent tool handler 或每次请求路径中直接创建 repository。

如果未来选择 lifespan / Celery hook 预初始化 repository，需要同时提供显式初始化和关闭流程，并保证 API 与 Worker 使用同一套 provider 约定；首版不采用该方案。

## 5. RAG Pipeline

首版固定 pipeline：

```text
request
-> normalize question
-> resolve requested scope and server-side allowed scope
-> build effective domain / kb filter
-> retrieve by hybrid search
-> post retrieval: dedup + rerank + document collapse
-> context assembly with budget
-> LLM structured answer
-> citation validation
-> trace / response data assembly
-> response
```

推荐默认参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `retrieval_mode` | `hybrid` | Milvus full-text 可用时默认 hybrid |
| `top_k` | 20 | 宽召回 |
| `final_k` | 5 | 进入 prompt 的证据数量 |
| `require_citations` | true | 生产问答默认要求引用 |
| `max_context_chars` | 12000 | 首版用字符预算，后续可升级 token 预算 |
| `answer_on_low_confidence` | false | 证据不足不强答 |

## 6. Vector Metadata

首版 vector 只保存检索、可见性过滤和 context assembly 必需字段，不把 citation 展示字段强塞进 Milvus schema。

| 字段 | 类型 | 必填 | 用途 |
| --- | --- | --- | --- |
| `doc_id` | int | 是 | 文档过滤、collapse、citation |
| `kb_id` | int | 是 | 普通知识库过滤，支持 `IN` |
| `domain` | string | 是 | Agent 业务域过滤 |
| `chunk_index` | int | 是 | context assembly 排序和定位 |

当前已迁移的 `internal/vectors/chunks.py` 支持：

- `doc_id`
- `kb_id`

生产 RAG 接入前必须继续扩展 `internal/vectors/chunks.py`：

- `CHUNK_ALLOWED_FILTER_FIELDS` 增加 `domain`。
- `ChunkVectorDocument` 增加 `domain` 和 `chunk_index`。
- `collection_spec.scalar_fields` 增加 `domain` 和 `chunk_index`。
- `to_records()` 写入 `domain` 和 `chunk_index` metadata。

首版不增加 `status` scalar field。发布态通过“只索引可见 chunk + 下线时删除 vector records”的索引策略保证。若未来需要在同一 collection 中保留草稿、下线或灰度内容，再引入 `status` filter，并同步迁移 collection schema。

注意：如果已有 Milvus collection 缺少 `kb_id`、`domain` 或 `chunk_index` scalar field，新增字段会触发 schema mismatch。生产接入前需要明确 collection 重建、迁移或兼容方案。

## 7. Evidence / Citation Contract

首版采用方案 B：vector 只负责召回和基础过滤，citation 展示信息由 RAG Service 在检索后通过 DAO 批量补齐。

ID 语义必须固定：

| 字段 | 语义 | 稳定性 |
| --- | --- | --- |
| `chunk_id` | 稳定 chunk 主键，即 `VectorRecord.id` / `ChunkVectorDocument.id` | 跨 run 稳定 |
| `evidence_id` | 本次 `rag_run` 内的 evidence alias，例如 `ev_001` | 只在本次 run 内有效 |

不要混用这两个概念。API response 可以同时返回 `evidence_id` 和 `chunk_id`，但评测 gold evidence 应使用稳定 `chunk_id`，不要使用 run-scoped `evidence_id`。

RAG Service 在检索后必须构建本次 evidence store：

```text
EvidenceStore = dict[evidence_id, RagEvidence]

RagEvidence
- evidence_id: str
- chunk_id: int
- doc_id: int
- kb_id: int | None
- title: str | None
- source_uri: str | None
- document_version: str | None
- text_hash: str
- content_excerpt: str
- score: float | None
- retrieval_mode: str | None
```

字段来源：

- `chunk_id`、`doc_id`、`kb_id`、`score`、`retrieval_mode` 来自 vector hit。
- `title`、`source_uri`、`document_version`、完整 chunk 元数据通过 `internal/dao/rag.py` 按 `chunk_id` / `doc_id` 批量补齐。
- `text_hash` 由 Service 对 chunk text 或最终 excerpt 计算，供 trace、评测和回归核对。
- DAO 补齐必须批量查询，禁止对每个 evidence 循环查库。

Citation validator 必须按以下规则实现：

```text
1. RagService 检索并构建 evidence_store。
2. Prompt/context 只暴露本次 evidence_id 和对应证据内容。
3. LLM structured answer 只能输出 citations: list[evidence_id]。
4. citation validator 只接受 evidence_store 中存在的 evidence_id。
5. validator 输出 API citation schema 时，从 evidence_store 映射出 chunk_id/doc_id/title/source_uri/document_version。
6. 模型自由生成的 chunk_id、title、source_uri、document_version 一律不可信，不能直接进入响应。
```

trace 和内部评测数据响应必须包含：

```text
evidence_id -> chunk_id / doc_id / kb_id / document_version / text_hash
```

这保证后续审计可以证明最终引用来自本次检索证据，而不是模型生成的任意引用。

## 8. Agent 接入点

当前迁移点：

```text
internal/agents/order/tools.py
-> search_order_knowledge

internal/agents/payment/tools.py
-> search_payment_knowledge
```

后续实现时不要在 tool handler 里写检索细节。推荐 tool 调用：

```python
await rag_service.retrieve(
    user_id=user_id,
    requested_domain="order",
    query=query,
    requested_kb_ids=kb_ids,
    top_k=top_k,
)
```

tool 返回给 ReAct 的 observation 应短而结构化：

```json
{
  "ok": true,
  "query": "发票开具通知",
  "matches": [
    {
      "evidence_id": "ev_001",
      "doc_id": 10,
      "kb_id": 2,
      "chunk_id": 1001,
      "title": "电子发票开具与通知",
      "content": "开票申请提交后通常会在 1-3 个工作日内完成...",
      "source_uri": "order/help/invoice",
      "score": 0.91
    }
  ],
  "total": 1
}
```

不要把大量原始 chunk 直接塞给 ReAct Controller。原文证据由 `RagService` 保存或结构化返回，最终回答时再按预算组装。

## 9. API 契约建议

### 9.1 用户态 RAG 问答

```text
POST /v1/rag/answer
```

请求：

```json
{
  "question": "发票开具后多久通知用户？",
  "requested_domain": "order",
  "requested_kb_ids": [2],
  "retrieval": {
    "mode": "hybrid",
    "top_k": 20,
    "final_k": 5
  },
  "require_citations": true
}
```

响应：

```json
{
  "run_id": "rag_run_001",
  "answer": "通常会在 1-3 个工作日内完成，完成后通过站内信和邮箱通知用户。",
  "citations": [
    {
      "evidence_id": "ev_001",
      "doc_id": 10,
      "kb_id": 2,
      "chunk_id": 1001,
      "title": "电子发票开具与通知",
      "source_uri": "order/help/invoice",
      "score": 0.91
    }
  ],
  "confidence": "medium",
  "trace_id": "trace_001"
}
```

### 9.2 内部评测数据接口

```text
POST /v1/internal/rag/evaluations/run
```

该接口走内部签名认证，用于评测系统同步获取单个 case 的 RAG 运行数据。服务端只执行 RAG 链路并返回数据，不计算评估指标、不判断 pass/fail、不实现评测算法。

请求：

```json
{
  "case_id": "case_001",
  "subject_user_id": 123,
  "question": "发票开具后多久通知用户？",
  "requested_domain": "order",
  "requested_kb_ids": [2],
  "retrieval": {
    "mode": "hybrid",
    "top_k": 20,
    "final_k": 5
  },
  "return_context": false
}
```

响应：

```json
{
  "case_id": "case_001",
  "rag_run_id": "rag_run_001",
  "answer": "通常会在 1-3 个工作日内完成...",
  "citations": [
    {
      "evidence_id": "ev_001",
      "doc_id": 10,
      "kb_id": 2,
      "chunk_id": 1001,
      "title": "电子发票开具与通知",
      "source_uri": "order/help/invoice",
      "score": 0.91
    }
  ],
  "retrieved_evidence": [
    {
      "evidence_id": "ev_001",
      "doc_id": 10,
      "kb_id": 2,
      "chunk_id": 1001,
      "content_excerpt": "开票申请提交后通常会在 1-3 个工作日内完成...",
      "text_hash": "sha256:...",
      "score": 0.91
    }
  ],
  "requested_domain": "order",
  "requested_kb_ids": [2],
  "effective_domains": ["order"],
  "effective_kb_ids": [2],
  "retrieval_mode": "hybrid",
  "latency_ms": 832,
  "errors": []
}
```

请求不接收 `expected_answer`、`expected_chunk_ids`、评测 `mode` 或 `persist`。这些属于外部评测系统的输入和评分逻辑，不进入本服务接口。

默认 `return_context=false`，避免返回大段原文或敏感内容。需要调试时再显式开启，并由 Service 控制最大返回长度。即使开启，也只返回本次 evidence store 中的受控 excerpt，不返回完整 prompt。

## 10. Trace 与审计

每次 RAG 请求建议记录：

| 字段 | 说明 |
| --- | --- |
| `run_id` | RAG run ID |
| `trace_id` | 请求 trace |
| `user_id` | 用户态请求用户或评测 subject user |
| `question` | 用户问题，必要时脱敏 |
| `requested_domain` | 请求期望业务域 |
| `requested_kb_ids` | 请求期望知识库 ID |
| `allowed_domains` | 服务端解析出的可用业务域 |
| `allowed_kb_ids` | 服务端解析出的可用知识库 ID |
| `effective_domains` | 最终检索业务域 |
| `effective_kb_ids` | 最终检索知识库 ID |
| `retrieval_mode` | dense / full_text / hybrid |
| `retrieved_chunk_ids` | 原始召回 chunk |
| `selected_evidence_ids` | 进入回答的证据 |
| `evidence_mappings` | `evidence_id -> chunk_id/doc_id/kb_id/document_version/text_hash` |
| `citations` | 最终引用 |
| `latency_ms` | 总耗时 |
| `error_code` | 失败时的稳定错误码 |

不要记录 secret、token、数据库连接串、完整认证 header 或超长 prompt 明文。若需要保存 prompt/context 快照，必须设置脱敏、大小限制和保留周期。

## 11. 回归检查清单

实现或修改 RAG 时至少检查：

- [ ] `kb_id` 是否通过普通 `FilterCondition` 传入，而不是 `scope_value`。
- [ ] 是否没有引入 `tenant_id` 依赖。
- [ ] 用户态请求是否只接收 `requested_domain/requested_kb_ids`，不把客户端输入当成最终授权范围。
- [ ] `RagService` 是否从服务端配置或 DB registry 解析 `allowed_domains/allowed_kb_ids`。
- [ ] 最终 vector filter 是否使用 `effective_domains/effective_kb_ids`，即 requested scope 和 allowed scope 的交集。
- [ ] 空交集是否按用户态拒答或内部评测稳定错误处理。
- [ ] 首版 vector metadata 是否包含必填 `doc_id/kb_id/domain/chunk_index`。
- [ ] 未发布、下线或禁用文档是否不会进入生产 RAG collection，或会被索引任务删除。
- [ ] Controller 是否只做 schema、依赖和 response mapping。
- [ ] Service 是否负责 filter、检索、生成、citation、trace。
- [ ] `new_rag_service()` 是否保持同步懒加载单例，不执行 async I/O。
- [ ] repository 是否通过 Service 内部 async lazy init 创建，并用 async lock 防并发重复初始化。
- [ ] 是否没有在每次请求、Controller 或 Agent tool handler 中调用 `new_chunk_repository()`。
- [ ] 测试是否可通过 fake repository 或 fake repository factory 替换真实 Milvus / embedding 依赖。
- [ ] Agent tool 是否只调用 Service，不直接访问 vector backend / DB / Redis。
- [ ] `evidence_id` 是否只作为 run-scoped alias，`chunk_id` 是否作为稳定 chunk ID。
- [ ] trace 和内部评测数据响应是否包含 `evidence_id -> chunk_id/doc_id/kb_id/document_version/text_hash` 映射。
- [ ] citation validator 是否只接受本次 evidence store 中存在的 `evidence_id`。
- [ ] API citation 中的 `title/source_uri/document_version` 是否来自 DAO 批量补齐，而不是模型输出。
- [ ] 内部评测数据接口是否只返回数据，不接收 gold answer/evidence，不计算 metrics，不判断 pass/fail。
- [ ] 检索是否限制 `top_k`、`final_k` 和 context budget。
- [ ] 引用是否来自本次可见证据。
- [ ] 低置信度或无证据时是否拒答或说明无法确认。
- [ ] trace 是否不泄露 secret、token、完整连接串或超长原文。
- [ ] Milvus collection schema 变更是否有重建或迁移说明。

## 12. 推荐实现顺序

1. 完成 `internal/vectors/chunks.py` 的 metadata schema 和 filter 行为。
2. 实现 `internal/services/rag.py` 的 `retrieve()`，替换订单/支付 mock 知识工具。
3. 实现 evidence store 和 DAO 批量补齐 citation 展示字段。
4. 实现 `answer()`，加入 context assembly、LLM structured answer 和 citation 校验。
5. 增加 `POST /v1/rag/answer`。
6. 增加 `POST /v1/internal/rag/evaluations/run`，只返回 RAG 运行数据。
7. 增加 RAG trace 持久化。
8. 配合外部评测系统用数据接口回归订单、支付、无证据、错误引用、prompt injection 负例。

## 13. 验证命令

迁移或修改 chunk vector repository 后优先运行：

```bash
uv run pytest tests/vector/repositories/test_chunks.py -q
uv run ruff check internal/vectors tests/vector/repositories/test_chunks.py
```

接入 Agent tool 后补跑：

```bash
uv run pytest tests/api/test_agent.py -q
uv run ruff check internal/agents internal/services internal/schemas internal/controllers tests/api/test_agent.py
```
