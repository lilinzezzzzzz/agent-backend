# `pkg.vectors.backends.milvus`

`pkg.vectors.backends.milvus` 是当前仓库在 Milvus 上的向量存储实现，负责把仓库内部统一的向量抽象层翻译成 `pymilvus.MilvusClient` 调用。

它不是一个“Milvus SDK 二次封装大全”，而是一个面向本仓库使用场景的 backend，目标是：

- 用统一的 `CollectionSpec / VectorRecord / SearchRequest` 驱动 Milvus
- 对业务层隐藏 Milvus schema / index / search 的细节
- 支持 dense retrieval、BM25/full-text、hybrid retrieval、rerank
- 尽量保持最小 API 面，避免业务代码直接依赖 Milvus SDK

核心源码：

- [__init__.py](./__init__.py)
- [backend.py](./backend.py)
- [schema.py](./schema.py)
- [codec.py](./codec.py)

相关上游抽象：

- [pkg/vectors/backends/base.py](../base.py)
- [pkg/vectors/contracts.py](../../contracts.py)
- [pkg/vectors/repositories/base.py](../../repositories/base.py)
- [pkg/vectors/post_retrieval.py](../../post_retrieval.py)
- [pkg/vectors/context_assembly.py](../../context_assembly.py)

## 1. 模块职责

### `__init__.py`

提供模块级入口：

- `create_milvus_backend()`
- `connect_milvus()`
- `disconnect_milvus()`
- `MilvusBackend`

适合：

- 应用初始化阶段创建 backend
- 健康检查或连通性探测

### `backend.py`

实现真正的 backend 行为：

- lazy 创建 `MilvusClient`
- collection 自动创建与校验
- `upsert / delete / fetch / search`
- dense / full-text / hybrid 搜索分流
- recoverable error 自动重试
- collection load / release 管理

这是业务最关心的文件。

### `schema.py`

负责把 `CollectionSpec` 翻译成 Milvus schema/index：

- 主键字段
- 文本字段
- dense vector 字段
- payload 字段
- scalar fields
- sparse vector 字段
- BM25 function
- dense index / sparse index

同时也负责校验已存在 collection 的 schema 是否与 `CollectionSpec` 一致。

### `codec.py`

负责 Python 对象与 Milvus 行/命中结果之间的转换：

- `VectorRecord -> row`
- `row -> VectorRecord`
- `hit -> SearchHit`
- filter condition -> Milvus expression

## 2. 这个 backend 解决了什么问题

如果业务代码直接用 `pymilvus`，通常会遇到这些问题：

- 每个调用点都要知道 collection schema 长什么样
- dense search、BM25、hybrid search 的参数分布在业务层
- query filter 表达式容易重复和分叉
- collection 是否存在、是否 load、client 是否掉线，需要每层自己处理
- 想从 dense 升级到 hybrid，业务调用点要一起改

`MilvusBackend` 的职责就是把这些事情收口。

对 RAG 来说，当前推荐路径是：

1. 保留 dense retrieval
2. 开启 BM25 / full-text search
3. 默认用 hybrid retrieval
4. 用 reranker 融合 dense 与 BM25 结果

这个 backend 已经支持上述路径。

## 3. 快速开始

### 3.1 创建 backend

```python
from pkg.vectors.backends.milvus import create_milvus_backend

backend = create_milvus_backend(
    uri="http://localhost:19530",
    token=None,
    db_name=None,
    timeout=10.0,
)
```

如果需要默认 dense 检索参数：

```python
from pkg.vectors.backends.milvus import MilvusBackend

backend = MilvusBackend(
    uri="http://localhost:19530",
    timeout=10.0,
    default_search_params={
        "metric_type": "COSINE",
        "params": {"ef": 128},
    },
)
```

说明：

- `uri`: Milvus 地址
- `token`: 认证 token，可为空
- `db_name`: Milvus 数据库名，可为空
- `timeout`: SDK 级超时
- `default_search_params`: dense search 的默认参数

### 3.2 定义 collection

最简单的 dense collection：

```python
from pkg.vectors.backends.base import MetricType
from pkg.vectors.backends.milvus.types import MilvusCollectionSpec

spec = MilvusCollectionSpec(
    name="chunk_vectors",
    dimension=1024,
    metric_type=MetricType.COSINE,
)
```

默认 dense index 使用 `HNSW`。`AUTOINDEX` 不再作为可用配置；如果没有显式传
`search_params.params.ef`，backend 会为 HNSW dense 检索自动设置 `ef=max(64, limit)`。

启用 BM25 / full-text / hybrid：

```python
from pkg.vectors.backends.base import MetricType, ScalarDataType, ScalarFieldSpec
from pkg.vectors.backends.milvus.types import (
    FullTextSearchSpec,
    MilvusCollectionSpec,
    MilvusSparseInvertedIndexConfig,
)

spec = MilvusCollectionSpec(
    name="chunk_vectors",
    dimension=1024,
    metric_type=MetricType.COSINE,
    scalar_fields=[
        ScalarFieldSpec(name="org_id", data_type=ScalarDataType.INT64),
        ScalarFieldSpec(name="doc_id", data_type=ScalarDataType.INT64),
        ScalarFieldSpec(name="status", data_type=ScalarDataType.STRING, max_length=32),
    ],
    full_text_search=FullTextSearchSpec(
        enabled=True,
        sparse_vector_field="text_sparse",
        function_name="text_bm25_emb",
        index_config=MilvusSparseInvertedIndexConfig(
            metric_type="BM25",
        ),
    ),
)
```

建表：

```python
await backend.ensure_collection(spec=spec)
```

## 4. Schema 是怎么映射到 Milvus 的

`CollectionSpec` 并不是原样透传给 Milvus，它会被转换为 Milvus collection schema。

默认字段映射：

| 逻辑字段 | 默认名称 | Milvus 类型 | 说明 |
| --- | --- | --- | --- |
| 主键 | `id` | `INT64` | 必填，非自增 |
| 文本 | `text` | `VARCHAR` | 存原始文本 |
| dense 向量 | `embedding` | `FLOAT_VECTOR` | 维度由 `dimension` 决定 |
| payload | `payload` | `JSON` | 可选 |

如果配置了 `scalar_fields`，会额外生成对应标量字段。

如果配置了 `full_text_search.enabled=True`，还会额外生成：

| 字段/对象 | 默认名称 | 类型 | 作用 |
| --- | --- | --- | --- |
| sparse vector field | `text_sparse` | `SPARSE_FLOAT_VECTOR` | 存 BM25/sparse 表示 |
| BM25 function | `text_bm25_emb` | `FunctionType.BM25` | 从 `text` 生成 sparse 表示 |
| sparse index | `idx_text_sparse` | `SPARSE_INVERTED_INDEX` | 支持 BM25/full-text 检索 |

文本字段在 full-text 模式下会开启 analyzer。

## 5. `ensure_collection()` 的行为

`ensure_collection(spec=...)` 不是“无脑创建”，它的逻辑是：

1. 如果 collection 已在当前 backend 进程里标记为 ensured，直接返回
2. 如果 collection 不存在：
   创建 schema
   创建 index
   设置 consistency level
   load collection
3. 如果 collection 已存在：
   读取 collection description
   校验字段、类型、向量维度、BM25 function 等是否匹配
   load collection

这意味着：

- 它具有幂等性
- 它不是 migration 工具
- 它不会自动把旧 collection 升级成支持 BM25 的新 schema

## 6. 数据模型

### 6.1 `VectorRecord`

写入和读取 Milvus 时使用的标准记录类型：

```python
from pkg.vectors.contracts import VectorRecord

record = VectorRecord(
    id=1,
    text="Milvus supports hybrid retrieval",
    embedding=[0.1] * 1024,
    metadata={"doc_id": 1001, "org_id": 42},
    payload={"source": "docs"},
)
```

字段含义：

- `id`: 主键，必须是正整数
- `text`: 原始文本
- `embedding`: dense 向量
- `metadata`: 会映射到 `scalar_fields`
- `payload`: 会写入 payload JSON 字段

### 6.2 `SearchRequest`

搜索入口统一使用 `SearchRequest`：

```python
from pkg.vectors.contracts import SearchRequest

request = SearchRequest(
    vector=[0.1] * 1024,
    query_text="hybrid retrieval",
    top_k=10,
)
```

关键字段：

| 字段 | 作用 |
| --- | --- |
| `vector` | dense query embedding |
| `query_text` | 原始查询文本 |
| `top_k` | 最终返回数量 |
| `filters` | 过滤条件 |
| `include_payload` | 是否返回 payload |
| `output_fields` | 额外输出字段 |
| `search_params` | dense search 参数 |
| `sparse_search_params` | sparse/BM25 search 参数 |
| `retrieval_mode` | 检索模式 |
| `candidate_top_k` | hybrid 每一路的候选召回数 |
| `reranker` | hybrid 融合策略 |
| `consistency_level` | 搜索一致性级别覆盖 |

## 7. 搜索模式总览

当前支持 4 种 `RetrievalMode`：

- `AUTO`
- `DENSE`
- `FULL_TEXT`
- `HYBRID`

对应逻辑如下：

| `query_text` | `vector` | `retrieval_mode` | 实际解析结果 | Milvus API |
| --- | --- | --- | --- | --- |
| 无 | 有 | `AUTO` | `DENSE` | `search()` |
| 有 | 无 | `AUTO` | `FULL_TEXT` | `search()` |
| 有 | 有 | `AUTO` | `HYBRID` | `hybrid_search()` |
| 无 | 有 | `DENSE` | `DENSE` | `search()` |
| 有 | 无/有 | `FULL_TEXT` | `FULL_TEXT` | `search()` |
| 有 | 有 | `HYBRID` | `HYBRID` | `hybrid_search()` |

约束：

- `DENSE` 必须有 `vector`
- `FULL_TEXT` 必须有 `query_text`
- `HYBRID` 必须同时有 `vector` 和 `query_text`
- `FULL_TEXT` 和 `HYBRID` 要求 collection 已启用 `full_text_search`
- `AUTO` 下如果同时提供了 `vector` 和 `query_text`，但 collection 没启用 full-text，backend 会显式报错

## 8. Dense retrieval

纯 dense retrieval 会走 `MilvusClient.search()`，检索字段是 `spec.vector_field`。

```python
from pkg.vectors.contracts import RetrievalMode, SearchRequest

request = SearchRequest(
    vector=[0.1] * 1024,
    top_k=10,
    retrieval_mode=RetrievalMode.DENSE,
)

hits = await backend.search(spec=spec, request=request)
```

自定义 dense search 参数：

```python
request = SearchRequest(
    vector=[0.1] * 1024,
    top_k=10,
    search_params={
        "metric_type": "COSINE",
        "params": {"ef": 128},
    },
)
```

适用场景：

- 只有 embedding query，没有原始 query text
- 你明确只想做 semantic recall
- 对召回成本比较敏感，不想多一路 BM25

## 9. Full-text retrieval

纯 full-text retrieval 也走 `MilvusClient.search()`，但检索字段改为 `spec.full_text_search.sparse_vector_field`。

```python
from pkg.vectors.contracts import RetrievalMode, SearchRequest

request = SearchRequest(
    query_text="how does hybrid retrieval work",
    top_k=10,
    retrieval_mode=RetrievalMode.FULL_TEXT,
)

hits = await backend.search(spec=spec, request=request)
```

自定义 sparse/BM25 参数：

```python
request = SearchRequest(
    query_text="llm reranker",
    top_k=10,
    retrieval_mode=RetrievalMode.FULL_TEXT,
    sparse_search_params={
        "metric_type": "BM25",
        "params": {},
    },
)
```

适用场景：

- query 是典型关键词检索
- 你暂时不想生成 query embedding
- 某些精确 token、命名实体、版本号、错误码，对 BM25 更敏感

## 10. Hybrid retrieval

Hybrid retrieval 会同时构造两路 `AnnSearchRequest`：

1. dense 分支
2. BM25 / sparse 分支

然后调用 `MilvusClient.hybrid_search()`。

### 10.1 默认 hybrid

```python
from pkg.vectors.contracts import SearchRequest

request = SearchRequest(
    vector=[0.1] * 1024,
    query_text="how does hybrid retrieval work",
    top_k=10,
)

hits = await backend.search(spec=spec, request=request)
```

这里没有显式设置 `retrieval_mode`，默认 `AUTO`。由于同时提供了 `vector` 和 `query_text`，会自动解析成 `HYBRID`。

### 10.2 candidate_top_k

```python
request = SearchRequest(
    vector=[0.1] * 1024,
    query_text="hybrid retrieval",
    top_k=10,
    candidate_top_k=30,
)
```

含义：

- `candidate_top_k`: dense 和 BM25 两路各自先召回多少候选
- `top_k`: rerank 后最终返回多少条

通常：

- `candidate_top_k >= top_k`
- 如果你希望 reranker 有更多可融合空间，可以适当调大 `candidate_top_k`

### 10.3 Reranker

当前支持两个 reranker：

- `RRF`
- `Weighted`

#### 默认 `RRF`

如果不传 `reranker`，backend 默认会用 `RRFRanker()`。

```python
request = SearchRequest(
    vector=[0.1] * 1024,
    query_text="hybrid retrieval",
    top_k=10,
)
```

适用场景：

- 想要更稳健的融合
- 不确定 dense 与 BM25 的权重应该怎么分配
- 想降低单一路召回分数尺度差异的影响

#### 显式 `RRF`

```python
from pkg.vectors.contracts import RerankerStrategy, SearchReranker

request = SearchRequest(
    vector=[0.1] * 1024,
    query_text="hybrid retrieval",
    top_k=10,
    reranker=SearchReranker(
        strategy=RerankerStrategy.RRF,
        k=60,
    ),
)
```

#### `Weighted`

```python
from pkg.vectors.contracts import (
    RetrievalMode,
    RerankerStrategy,
    SearchRequest,
    SearchReranker,
)

request = SearchRequest(
    vector=[0.1] * 1024,
    query_text="hybrid retrieval",
    top_k=10,
    retrieval_mode=RetrievalMode.HYBRID,
    candidate_top_k=30,
    reranker=SearchReranker(
        strategy=RerankerStrategy.WEIGHTED,
        weights=[0.7, 0.3],
        normalize_score=True,
    ),
)
```

当前 `weights` 的顺序是固定的：

1. dense 分支
2. BM25 / sparse 分支

也就是说：

- `weights=[0.8, 0.2]`: 更偏 dense
- `weights=[0.5, 0.5]`: 平衡
- `weights=[0.3, 0.7]`: 更偏 BM25

约束：

- `Weighted` 必须提供 `weights`
- 当前 hybrid 固定只有两路，所以权重数量必须是 2

## 11. 基本 CRUD 操作

### 11.1 Upsert

```python
from pkg.vectors.contracts import VectorRecord

records = [
    VectorRecord(
        id=1,
        text="Milvus supports hybrid retrieval",
        embedding=[0.1] * 1024,
        metadata={
            "org_id": 42,
            "doc_id": 1001,
            "status": "active",
        },
        payload={"source": "docs"},
    )
]

await backend.upsert(spec=spec, records=records)
```

要求：

- `id > 0`
- `embedding` 不能为空
- `embedding` 维度必须与 `spec.dimension` 一致

### 11.2 Delete

按 id 删除：

```python
deleted = await backend.delete(spec=spec, ids=[1, 2, 3])
```

按 filter 删除：

```python
from pkg.vectors.contracts import FilterCondition, FilterOperator

deleted = await backend.delete(
    spec=spec,
    filters=[
        FilterCondition(field="org_id", op=FilterOperator.EQ, value=42),
        FilterCondition(field="status", op=FilterOperator.EQ, value="inactive"),
    ],
)
```

### 11.3 Fetch

按 id fetch：

```python
records = await backend.fetch(spec=spec, ids=[1, 2, 3])
```

按过滤条件 fetch：

```python
from pkg.vectors.contracts import ConsistencyLevel, FilterCondition, FilterOperator

records = await backend.fetch(
    spec=spec,
    filters=[
        FilterCondition(field="org_id", op=FilterOperator.EQ, value=42),
    ],
    limit=20,
    consistency_level=ConsistencyLevel.STRONG,
)
```

注意：

- `fetch()` 现在要求至少提供 `ids` 或 `filters`
- 不允许无条件整表扫描
- `limit` 会尽量下推到 Milvus，而不是只在 Python 侧切片

## 12. Filter 是怎么工作的

业务层传入的 `FilterCondition` 会被翻译成 Milvus expression。

支持操作：

- `EQ`
- `NE`
- `IN`
- `LT`
- `LTE`
- `GT`
- `GTE`

示例：

```python
from pkg.vectors.contracts import FilterCondition, FilterOperator

filters = [
    FilterCondition(field="org_id", op=FilterOperator.EQ, value=42),
    FilterCondition(field="doc_id", op=FilterOperator.IN, value=[1001, 1002, 1003]),
]
```

会转换成类似：

```text
(org_id == 42) and (doc_id in [1001, 1002, 1003])
```

注意：

- `IN` 的 `value` 必须是 list
- 其他比较操作的 `value` 不能是 list
- 字符串会自动做转义

## 13. SearchHit 返回什么

返回结果类型是 `SearchHit`。

典型字段：

- `id`
- `text`
- `metadata`
- `payload`
- `raw_score`
- `relevance_score`

说明：

- `raw_score`: Milvus 原始返回分数
- `relevance_score`: backend 暴露给上层的分数

当前分数处理策略：

- dense retrieval:
  - `COSINE` / `IP` 基本直接使用原始分数
  - `L2` 会做简单归一化
- full-text / hybrid:
  - 当前直接使用 Milvus 返回分数

因此：

- 不同 retrieval mode 下的分数不能直接横向比较
- 更不应该跨 dense / BM25 / hybrid 用统一阈值硬切

## 14. Consistency Level

`CollectionSpec` 上有默认 `consistency_level`，`fetch()` 和 `search()` 也支持按请求覆盖。

默认：

```python
ConsistencyLevel.SESSION
```

覆盖示例：

```python
from pkg.vectors.contracts import ConsistencyLevel, SearchRequest

request = SearchRequest(
    vector=[0.1] * 1024,
    top_k=10,
    consistency_level=ConsistencyLevel.STRONG,
)
```

适合：

- 默认情况用 `SESSION`
- 对刚写入数据的强一致读取场景，按请求切到 `STRONG`

## 15. 生命周期管理

### 15.1 健康检查

```python
status = await backend.healthcheck()
```

返回类似：

```python
{
    "backend": "milvus",
    "status": "ok",
    "version": "2.x.x",
}
```

### 15.2 释放 collection

```python
await backend.release_collection(collection_name=spec.name)
```

作用：

- release Milvus collection
- 清理 backend 内部的 loaded / ensured 状态

### 15.3 关闭 backend

```python
backend.close()
```

作用：

- 关闭 client
- 清理缓存状态
- backend 之后仍可重新创建 client

### 15.4 彻底 shutdown

```python
backend.shutdown()
```

作用：

- 关闭 client
- 标记 backend 已 shutdown
- 后续不允许再重建 client

## 16. 错误恢复机制

`MilvusBackend` 对部分客户端错误做了自动恢复。

可恢复错误包括：

- `ConnectError`
- `ConnectionNotExistException`
- `MilvusUnavailableException`
- 部分 gRPC 错误：
  - `UNAVAILABLE`
  - `DEADLINE_EXCEEDED`
  - `CANCELLED`

恢复逻辑：

1. 当前操作失败
2. 判断是否是可恢复错误
3. reset client 状态
4. 清空 loaded / ensured collection 缓存
5. 重试一次操作

这保证了：

- 网络闪断时业务层不必自己重建 backend
- collection 状态缓存不会在 client 失效后变脏

## 17. 与 `BaseVectorRepository` 的协作方式

大多数业务不会直接调用 backend，而是通过 repository。

当前调用链通常是：

1. `repository.search_by_text(query_text=...)`
2. repository 调 embedder 生成 `query_vector`
3. repository 构造 `SearchRequest(vector=..., query_text=...)`
4. backend 按 `RetrievalMode.AUTO` 自动分流

这意味着：

- 如果 collection 没开 full-text：
  默认 `search_by_text()` 会因为 `AUTO` 解析成 `HYBRID` 而显式报错
- 如果 collection 开了 full-text：
  `search_by_text()` 会自然升级成 hybrid retrieval

业务层不需要显式写：

- 先 dense 搜一次
- 再 BM25 搜一次
- 再手工融合

如果你明确只想走 lexical/BM25，不想先做 embedding，可以直接：

```python
from pkg.vectors.contracts import RetrievalMode

hits = await repository.search_by_text(
    query_text="ConnectionNotExistException",
    top_k=10,
    retrieval_mode=RetrievalMode.FULL_TEXT,
)
```

这条路径不会先调用 embedder。

### 17.1 `retrieve_*` 和 `assemble_context_*`

如果你要做的是标准 RAG，而不是单纯“拿回一批 hit”，repository 现在还提供了两层更高阶的入口：

- `retrieve_by_text() / retrieve_by_vector()`
- `assemble_context_by_text() / assemble_context_by_vector()`

推荐理解成三层能力：

| repository 方法 | 输出 | 适合场景 |
| --- | --- | --- |
| `search_by_text()` / `search_by_vector()` | `list[SearchHit]` | 只关心原始召回结果 |
| `retrieve_by_text()` / `retrieve_by_vector()` | `PostRetrievalResult` | 需要 chunk dedup、document collapse、rerank |
| `assemble_context_by_text()` / `assemble_context_by_vector()` | `ContextAssemblyResult` | 需要直接产出给 LLM 的 context text |

对应关系是：

1. `search_*`: 只做 backend retrieval
2. `retrieve_*`: retrieval + post-retrieval pipeline
3. `assemble_context_*`: retrieval + post-retrieval + document context assembly

## 18. Post-Retrieval Pipeline

`pkg.vectors.post_retrieval` 是 Milvus backend 之上的一层通用 RAG 后处理能力，不绑定 Milvus SDK。

当前提供：

- chunk dedup
- document collapse
- chunk/document 两级 reranker
- 结构化统计信息

### 18.1 `retrieve_by_text()` 示例

```python
from pkg.vectors.post_retrieval import (
    CollapseConfig,
    DedupConfig,
    PostRetrievalConfig,
    PostRetrievalPipeline,
)

result = await repository.retrieve_by_text(
    query_text="ConnectionNotExistException",
    top_k=20,
    post_retrieval=PostRetrievalPipeline(
        config=PostRetrievalConfig(
            dedup=DedupConfig(
                key_fields=["text"],
            ),
            collapse=CollapseConfig(
                key_fields=["metadata.doc_id", "payload.doc_id"],
                max_chunks_per_document=3,
                max_documents=5,
            ),
        )
    ),
)
```

返回结果：

- `result.hits`: dedup/rerank 后的 chunk hits
- `result.documents`: collapse 后的 document 结果
- `result.stats`: 输入 hit 数、collapse 后 doc 数等统计

### 18.2 `CollapsedSearchHit` 里有什么

每个 `CollapsedSearchHit` 会保留：

- `document_key`
- `primary_hit_id`
- `hit_count`
- `chunk_ids`
- `chunks`
- `text / metadata / payload`
- `relevance_score / raw_score`
- `retrieval_mode`

也就是说，这一层不会把 source chunk 边界丢掉，后面还可以继续做：

- context assembly
- citation/source tracing
- 更强的 reranker
- doc-level packing

## 19. Document Context Assembly

`pkg.vectors.context_assembly` 负责把 `PostRetrievalResult.documents` 进一步组装成适合直接喂给 LLM 的上下文。

和直接拼 prompt 字符串不同，这一层会同时返回：

- 结构化的 document/window/chunk 结果
- 最终可直接使用的 `context_text`
- 截断与预算统计

### 19.1 默认行为

默认 `DocumentContextAssembler` 会：

- 按 `chunk_index -> position -> order -> start_offset -> id` 排序 chunk
- 把相邻 chunk 合并成一个 window
- 给每个 document 生成 `title/source/document_key` header
- 可选给每个 window 生成 `section/page/chunk_ids` header
- 按 document 级和 total 级字符预算截断

注意：

- 这一版只处理 post-retrieval 已经拿到的 top chunks
- 不会自动回 Milvus 拉取命中 chunk 的前后相邻 chunk
- 如果你要做真正的 neighbor expansion，需要在更上层显式补查

### 19.2 `assemble_context_by_text()` 示例

```python
from pkg.vectors.context_assembly import (
    ContextAssemblyConfig,
    ContextBudgetConfig,
    ContextWindowConfig,
    DocumentContextAssembler,
)
from pkg.vectors.post_retrieval import PostRetrievalPipeline

context = await repository.assemble_context_by_text(
    query_text="how does hybrid retrieval work",
    top_k=20,
    post_retrieval=PostRetrievalPipeline(),
    context_assembler=DocumentContextAssembler(
        config=ContextAssemblyConfig(
            window=ContextWindowConfig(
                include_headers=True,
                max_chunks_per_window=3,
            ),
            budget=ContextBudgetConfig(
                max_documents=5,
                max_total_chars=6000,
                max_document_chars=1500,
            ),
        )
    ),
)
```

### 19.3 `ContextAssemblyResult` 返回什么

`ContextAssemblyResult` 里有三个核心部分：

| 字段 | 作用 |
| --- | --- |
| `documents` | 结构化 document context 列表 |
| `context_text` | 最终拼好的上下文文本 |
| `stats` | document/chunk/window 数量与截断统计 |

每个 `documents[i]` 又会保留：

- `header`
- `title`
- `source`
- `chunk_ids`
- `windows`
- `text`
- `truncated`

其中 `windows` 里会继续保留：

- `window_index`
- `chunk_ids`
- `section`
- `page`
- `chunks`
- `text`
- `truncated`

### 19.4 适合放在什么地方

推荐把 `assemble_context_*()` 作为“最终喂给 LLM 之前”的最后一步：

1. 用户 query 进入 repository
2. repository 调 backend 做 dense / BM25 / hybrid retrieval
3. post-retrieval 做 dedup / collapse / rerank
4. context assembly 把 top chunks 组织成最终上下文
5. 上层 prompt builder 再把 `context.context_text` 放进 prompt template

不要把这层职责塞进：

- backend
- embedder
- prompt template

否则后面做 citation、source 展示、chunk/window 调优时会很难维护。

## 20. 推荐接入方式

如果你的 collection 没开 full-text，但又想继续走 `search_by_text()`，要显式指定：

```python
from pkg.vectors.contracts import RetrievalMode

hits = await repository.search_by_text(
    query_text="milvus",
    top_k=10,
    retrieval_mode=RetrievalMode.DENSE,
)
```

这条路径会先做 query embedding，然后只走 dense retrieval。

### 20.1 新项目

推荐：

1. collection 一开始就开启 `full_text_search`
2. query 入口默认使用 `repository.assemble_context_by_text()`
3. `retrieval_mode` 默认保留 `AUTO`
4. hybrid reranker 默认使用 `RRF`
5. post-retrieval 默认开启 dedup + collapse
6. 用 context assembly 控制 document/window/header/budget

这是当前最稳妥的默认方案。

### 20.2 从旧 dense collection 升级

如果你现在只有 dense collection，不要以为把 `full_text_search.enabled` 改成 `True` 就能自动升级。

因为 `ensure_collection()` 会校验现有 schema，一旦发现缺少这些对象就会报错：

- sparse field
- BM25 function
- sparse index
- text analyzer

正确做法通常是：

1. 新建一张支持 BM25 的 collection
2. 全量回灌数据
3. 切换读流量
4. 再决定是否淘汰旧 collection

### 20.3 什么时候只用 dense

下面场景可以先不启用 hybrid：

- query 只有 embedding，没有原始 query text
- 数据体量不大，dense 已足够
- 当前目标是最小化召回成本

### 20.4 什么时候优先 hybrid

下面场景更适合 hybrid：

- query 同时有语义和关键词需求
- 文档里有版本号、产品名、错误码、函数名、实体名
- 需要提高“关键词不丢，语义也不丢”的召回效果

## 21. 常见坑

### 21.1 旧 collection schema 不匹配

症状：

- `ensure_collection()` 报 `CollectionSchemaMismatchError`

原因：

- 线上已有 collection 的字段类型、维度、BM25 function 与 `CollectionSpec` 不一致

处理：

- 不要硬改 spec 试图绕过
- 明确做 schema migration 或重建 collection

### 21.2 `AUTO` 不是智能路由

`AUTO` 是确定性规则，不是“自动选择最佳检索策略”的模型判断器。

当前规则是：

- `vector + query_text` -> `HYBRID`
- `vector only` -> `DENSE`
- `query_text only` -> `FULL_TEXT`
- `vector + query_text` 且 collection 没启用 full-text -> 显式报错

### 21.3 `Weighted` 权重数不对

当前 hybrid 固定两路召回：

1. dense
2. BM25

所以必须传两个权重，例如：

```python
weights=[0.7, 0.3]
```

### 21.4 直接比较不同模式的 score

不要做这种事：

- “dense 0.82 和 BM25 0.82 一样好”
- “hybrid score < 0.7 就丢掉”

不同模式的分数语义并不一致。

### 21.5 把 prompt 拼接塞进 backend

不要让 `MilvusBackend` 直接负责：

- 拼 prompt
- 拼 citation
- 估 token 预算
- 合并相邻 chunk

这些属于 post-retrieval / context assembly 层，不属于 backend。

### 21.6 误以为 context assembly 会自动补齐相邻 chunk

当前 `DocumentContextAssembler` 只组装已经召回并 collapse 出来的 chunks。

它不会自动：

- 往前补一个 chunk
- 往后补一个 chunk
- 按 doc_id 再去数据库取整段上下文

如果你的 RAG 需要邻接 chunk 扩窗，要单独实现。

## 22. 最小实战示例

下面给一个最接近实际 RAG 的最小示例。

### 22.1 建 collection

```python
from pkg.vectors.backends.base import MetricType
from pkg.vectors.backends.milvus.types import FullTextSearchSpec, MilvusCollectionSpec

spec = MilvusCollectionSpec(
    name="rag_chunks",
    dimension=1024,
    metric_type=MetricType.COSINE,
    full_text_search=FullTextSearchSpec(enabled=True),
)

await backend.ensure_collection(spec=spec)
```

### 22.2 写入 chunk

```python
from pkg.vectors.contracts import VectorRecord

await backend.upsert(
    spec=spec,
    records=[
        VectorRecord(
            id=1,
            text="Milvus hybrid retrieval combines dense and BM25 search",
            embedding=[0.1] * 1024,
            payload={"doc_id": "doc-1"},
        ),
        VectorRecord(
            id=2,
            text="RRF is a robust reranking strategy for hybrid retrieval",
            embedding=[0.2] * 1024,
            payload={"doc_id": "doc-2"},
        ),
    ],
)
```

### 22.3 hybrid 搜索

```python
from pkg.vectors.contracts import SearchRequest

hits = await backend.search(
    spec=spec,
    request=SearchRequest(
        vector=[0.15] * 1024,
        query_text="how does hybrid retrieval work in milvus",
        top_k=5,
    ),
)
```

### 22.4 通过 repository 直接产出 LLM context

```python
from pkg.vectors.context_assembly import DocumentContextAssembler
from pkg.vectors.post_retrieval import PostRetrievalPipeline

context = await repository.assemble_context_by_text(
    query_text="how does hybrid retrieval work in milvus",
    top_k=10,
    post_retrieval=PostRetrievalPipeline(),
    context_assembler=DocumentContextAssembler(),
)
```

### 22.5 结果处理

```python
print(context.context_text)

for document in context.documents:
    print(document.document_key, document.chunk_ids, document.truncated)
```

## 23. 最后建议

如果你只是想“在当前系统里把 Milvus 用对”，推荐默认策略是：

1. collection 开启 `full_text_search`
2. 写入时继续保留 dense embedding
3. 查询入口用 `assemble_context_by_text()`
4. `retrieval_mode` 维持 `AUTO`
5. 默认让系统走 hybrid retrieval
6. reranker 默认使用 `RRF`
7. post-retrieval 默认做 dedup + collapse
8. 用 context assembly 统一控制 LLM context 的 header、window、budget

只有在你已经有明确评估结论时，再去调：

- `candidate_top_k`
- `search_params`
- `sparse_search_params`
- `Weighted` 的权重
- `CollapseConfig`
- `ContextAssemblyConfig`
