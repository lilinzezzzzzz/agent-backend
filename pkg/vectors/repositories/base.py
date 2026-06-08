from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, cast

from pkg.vectors.backends.base import CollectionSpec, IsolationMode, ScalarDataType, ScalarFieldSpec, VectorBackend
from pkg.vectors.context_assembly import ContextAssemblyResult, DocumentContextAssembler
from pkg.vectors.contracts import (
    ConsistencyLevel,
    FilterCondition,
    FilterOperator,
    RetrievalMode,
    ScalarValue,
    SearchHit,
    SearchRequest,
    SearchReranker,
    VectorRecord,
)
from pkg.embeddings.base import Embedder
from pkg.vectors.errors import RecordValidationError
from pkg.vectors.post_retrieval import PostRetrievalPipeline, PostRetrievalResult

type ScopeValue = int | str


class BaseVectorRepository[T](ABC):
    """向量仓储基类。

    提供向量数据库的通用 CRUD 操作抽象，子类只需实现：
    - collection_spec: 定义集合 schema
    - to_records: 将业务实体转换为 VectorRecord

    核心职责：
    1. 实体 <-> VectorRecord 转换
    2. 自动补全 embedding（若实体未提供）
    3. 可选数据隔离（显式配置 SHARED_FILTER + scope_field + scope_value 后生效）
    4. 统一的 upsert/delete/fetch/search 接口
    """

    def __init__(
        self,
        *,
        backend: VectorBackend,  # 向量数据库后端（Milvus/Qdrant 等）
        embedder: Embedder,  # 文本向量化器
        scope_value: ScopeValue | None = None,  # 显式启用 SHARED_FILTER 隔离时使用
    ) -> None:
        self._backend = backend
        self._embedder = embedder
        self._scope_value = scope_value

    @property
    def backend(self) -> VectorBackend:
        return self._backend

    @property
    def embedder(self) -> Embedder:
        return self._embedder

    @property
    def scope_value(self) -> ScopeValue | None:
        return self._scope_value

    @property
    @abstractmethod
    def collection_spec(self) -> CollectionSpec:
        """返回 repository 绑定的 collection spec。"""

    @property
    def scope_field(self) -> str | None:
        """数据隔离字段名，子类可覆写（如 'org_id'）。默认不启用数据隔离。"""
        return None

    @abstractmethod
    def to_records(self, *, entity: T) -> list[VectorRecord]:
        """将业务实体映射成可写入向量库的记录。

        一个实体可能对应多条记录（如一个文档拆分成多个 chunk）。
        """

    def build_scope_filters(self) -> list[FilterCondition]:
        """构建 scope 隔离过滤条件，自动附加到所有查询操作。"""
        spec = self.collection_spec
        scope_field = self.scope_field

        if spec.isolation_mode != IsolationMode.SHARED_FILTER:
            return []

        if scope_field is None:
            raise ValueError("isolation_mode=shared_filter 需要显式配置 scope_field")
        if self.scope_value is None:
            raise ValueError("isolation_mode=shared_filter 需要显式传入 scope_value")
        scope_scalar_field = find_scalar_field(spec=spec, field_name=scope_field)
        if scope_scalar_field is None:
            raise ValueError(f"scope_field 必须出现在 collection_spec.scalar_fields 中: field={scope_field}")
        validate_scope_value_type(field=scope_scalar_field, value=self.scope_value)

        return [
            FilterCondition(
                field=scope_field,
                op=FilterOperator.EQ,
                value=self.scope_value,
            )
        ]

    def build_default_search_filters(self) -> list[FilterCondition]:
        """构建默认搜索过滤条件，子类可覆写添加额外过滤（如排除已删除记录）。"""
        return []

    async def ensure_collection(self) -> None:
        await self.backend.ensure_collection(spec=self.collection_spec)

    async def upsert_entity(self, *, entity: T) -> None:
        await self.upsert_records(records=self.to_records(entity=entity))

    async def upsert_entities(self, *, entities: Sequence[T]) -> None:
        records: list[VectorRecord] = []
        for entity in entities:
            records.extend(self.to_records(entity=entity))
        await self.upsert_records(records=records)

    async def upsert_records(self, *, records: Sequence[VectorRecord]) -> None:
        """写入向量记录。自动补全缺失的 embedding 并确保集合存在。"""
        if not records:
            return
        prepared_records = await self._prepare_records(records=records)
        await self.ensure_collection()
        await self.backend.upsert(
            spec=self.collection_spec,
            records=prepared_records,
        )

    async def delete_by_ids(self, *, ids: Sequence[int]) -> int:
        return await self.backend.delete(
            spec=self.collection_spec,
            ids=ids,
            filters=self.build_scope_filters(),
        )

    async def delete_by_filters(self, *, filters: Sequence[FilterCondition]) -> int:
        scoped_filters = [*self.build_scope_filters(), *filters]
        return await self.backend.delete(
            spec=self.collection_spec,
            filters=scoped_filters,
        )

    async def fetch_by_ids(
        self,
        *,
        ids: Sequence[int],
        consistency_level: ConsistencyLevel | None = None,
    ) -> list[VectorRecord]:
        return await self.backend.fetch(
            spec=self.collection_spec,
            ids=ids,
            filters=self.build_scope_filters(),
            consistency_level=consistency_level,
        )

    async def fetch_by_filters(
        self,
        *,
        filters: Sequence[FilterCondition],
        limit: int | None = None,
        consistency_level: ConsistencyLevel | None = None,
    ) -> list[VectorRecord]:
        scoped_filters = [*self.build_scope_filters(), *filters]
        return await self.backend.fetch(
            spec=self.collection_spec,
            filters=scoped_filters,
            limit=limit,
            consistency_level=consistency_level,
        )

    async def search_by_text(
        self,
        *,
        query_text: str,
        top_k: int,
        filters: Sequence[FilterCondition] = (),
        include_payload: bool = False,
        output_fields: Sequence[str] = (),
        search_params: dict[str, Any] | None = None,
        sparse_search_params: dict[str, Any] | None = None,
        retrieval_mode: RetrievalMode = RetrievalMode.AUTO,
        candidate_top_k: int | None = None,
        reranker: SearchReranker | None = None,
        consistency_level: ConsistencyLevel | None = None,
    ) -> list[SearchHit]:
        query_vector: list[float] | None = None
        if retrieval_mode != RetrievalMode.FULL_TEXT:
            query_vector = await self.embedder.embed_query(text=query_text)

        request = self._build_search_request(
            query_vector=query_vector,
            query_text=query_text,
            top_k=top_k,
            filters=filters,
            include_payload=include_payload,
            output_fields=output_fields,
            search_params=search_params,
            sparse_search_params=sparse_search_params,
            retrieval_mode=retrieval_mode,
            candidate_top_k=candidate_top_k,
            reranker=reranker,
            consistency_level=consistency_level,
        )
        return await self.backend.search(
            spec=self.collection_spec,
            request=request,
        )

    async def retrieve_by_text(
        self,
        *,
        query_text: str,
        top_k: int,
        filters: Sequence[FilterCondition] = (),
        include_payload: bool = False,
        output_fields: Sequence[str] = (),
        search_params: dict[str, Any] | None = None,
        sparse_search_params: dict[str, Any] | None = None,
        retrieval_mode: RetrievalMode = RetrievalMode.AUTO,
        candidate_top_k: int | None = None,
        reranker: SearchReranker | None = None,
        post_retrieval: PostRetrievalPipeline | None = None,
        consistency_level: ConsistencyLevel | None = None,
    ) -> PostRetrievalResult:
        query_vector: list[float] | None = None
        if retrieval_mode != RetrievalMode.FULL_TEXT:
            query_vector = await self.embedder.embed_query(text=query_text)

        request = self._build_search_request(
            query_vector=query_vector,
            query_text=query_text,
            top_k=top_k,
            filters=filters,
            include_payload=include_payload,
            output_fields=output_fields,
            search_params=search_params,
            sparse_search_params=sparse_search_params,
            retrieval_mode=retrieval_mode,
            candidate_top_k=candidate_top_k,
            reranker=reranker,
            consistency_level=consistency_level,
        )
        hits = await self.backend.search(
            spec=self.collection_spec,
            request=request,
        )
        pipeline = post_retrieval or PostRetrievalPipeline()
        return await pipeline.run(
            hits=hits,
            query_text=query_text,
            query_vector=query_vector,
        )

    async def assemble_context_by_text(
        self,
        *,
        query_text: str,
        top_k: int,
        filters: Sequence[FilterCondition] = (),
        include_payload: bool = False,
        output_fields: Sequence[str] = (),
        search_params: dict[str, Any] | None = None,
        sparse_search_params: dict[str, Any] | None = None,
        retrieval_mode: RetrievalMode = RetrievalMode.AUTO,
        candidate_top_k: int | None = None,
        reranker: SearchReranker | None = None,
        post_retrieval: PostRetrievalPipeline | None = None,
        context_assembler: DocumentContextAssembler | None = None,
        consistency_level: ConsistencyLevel | None = None,
    ) -> ContextAssemblyResult:
        """执行 text retrieval，并把 collapsed documents 组装成 LLM context。"""
        retrieval_result = await self.retrieve_by_text(
            query_text=query_text,
            top_k=top_k,
            filters=filters,
            include_payload=include_payload,
            output_fields=output_fields,
            search_params=search_params,
            sparse_search_params=sparse_search_params,
            retrieval_mode=retrieval_mode,
            candidate_top_k=candidate_top_k,
            reranker=reranker,
            post_retrieval=post_retrieval,
            consistency_level=consistency_level,
        )
        assembler = context_assembler or DocumentContextAssembler()
        return assembler.assemble(result=retrieval_result)

    async def search_by_vector(
        self,
        *,
        query_vector: list[float],
        query_text: str | None = None,
        top_k: int,
        filters: Sequence[FilterCondition] = (),
        include_payload: bool = False,
        output_fields: Sequence[str] = (),
        search_params: dict[str, Any] | None = None,
        sparse_search_params: dict[str, Any] | None = None,
        retrieval_mode: RetrievalMode = RetrievalMode.AUTO,
        candidate_top_k: int | None = None,
        reranker: SearchReranker | None = None,
        consistency_level: ConsistencyLevel | None = None,
    ) -> list[SearchHit]:
        request = self._build_search_request(
            query_vector=query_vector,
            query_text=query_text,
            top_k=top_k,
            filters=filters,
            include_payload=include_payload,
            output_fields=output_fields,
            search_params=search_params,
            sparse_search_params=sparse_search_params,
            retrieval_mode=retrieval_mode,
            candidate_top_k=candidate_top_k,
            reranker=reranker,
            consistency_level=consistency_level,
        )
        return await self.backend.search(
            spec=self.collection_spec,
            request=request,
        )

    async def retrieve_by_vector(
        self,
        *,
        query_vector: list[float],
        query_text: str | None = None,
        top_k: int,
        filters: Sequence[FilterCondition] = (),
        include_payload: bool = False,
        output_fields: Sequence[str] = (),
        search_params: dict[str, Any] | None = None,
        sparse_search_params: dict[str, Any] | None = None,
        retrieval_mode: RetrievalMode = RetrievalMode.AUTO,
        candidate_top_k: int | None = None,
        reranker: SearchReranker | None = None,
        post_retrieval: PostRetrievalPipeline | None = None,
        consistency_level: ConsistencyLevel | None = None,
    ) -> PostRetrievalResult:
        request = self._build_search_request(
            query_vector=query_vector,
            query_text=query_text,
            top_k=top_k,
            filters=filters,
            include_payload=include_payload,
            output_fields=output_fields,
            search_params=search_params,
            sparse_search_params=sparse_search_params,
            retrieval_mode=retrieval_mode,
            candidate_top_k=candidate_top_k,
            reranker=reranker,
            consistency_level=consistency_level,
        )
        hits = await self.backend.search(
            spec=self.collection_spec,
            request=request,
        )
        pipeline = post_retrieval or PostRetrievalPipeline()
        return await pipeline.run(
            hits=hits,
            query_text=query_text,
            query_vector=query_vector,
        )

    async def assemble_context_by_vector(
        self,
        *,
        query_vector: list[float],
        query_text: str | None = None,
        top_k: int,
        filters: Sequence[FilterCondition] = (),
        include_payload: bool = False,
        output_fields: Sequence[str] = (),
        search_params: dict[str, Any] | None = None,
        sparse_search_params: dict[str, Any] | None = None,
        retrieval_mode: RetrievalMode = RetrievalMode.AUTO,
        candidate_top_k: int | None = None,
        reranker: SearchReranker | None = None,
        post_retrieval: PostRetrievalPipeline | None = None,
        context_assembler: DocumentContextAssembler | None = None,
        consistency_level: ConsistencyLevel | None = None,
    ) -> ContextAssemblyResult:
        """执行 vector retrieval，并把 collapsed documents 组装成 LLM context。"""
        retrieval_result = await self.retrieve_by_vector(
            query_vector=query_vector,
            query_text=query_text,
            top_k=top_k,
            filters=filters,
            include_payload=include_payload,
            output_fields=output_fields,
            search_params=search_params,
            sparse_search_params=sparse_search_params,
            retrieval_mode=retrieval_mode,
            candidate_top_k=candidate_top_k,
            reranker=reranker,
            post_retrieval=post_retrieval,
            consistency_level=consistency_level,
        )
        assembler = context_assembler or DocumentContextAssembler()
        return assembler.assemble(result=retrieval_result)

    async def _prepare_records(self, *, records: Sequence[VectorRecord]) -> list[VectorRecord]:
        """预处理记录：为缺失 embedding 的记录批量生成向量。

        流程：
        1. 找出所有 embedding 为 None 的记录索引
        2. 批量调用 embedder 生成向量（减少 API 调用次数）
        3. 将生成的向量填充回对应记录
        """
        missing_embedding_indexes = [index for index, record in enumerate(records) if record.embedding is None]
        if not missing_embedding_indexes:
            return list(records)

        texts = [records[index].text for index in missing_embedding_indexes]
        embeddings = await self.embedder.embed_texts(texts=texts)
        if len(embeddings) != len(missing_embedding_indexes):
            raise RecordValidationError(
                f"embedding 返回数量不匹配: got={len(embeddings)}, expected={len(missing_embedding_indexes)}"
            )

        prepared_records = list(records)
        for index, embedding in zip(missing_embedding_indexes, embeddings, strict=True):
            prepared_records[index] = prepared_records[index].model_copy(update={"embedding": embedding})
        return prepared_records

    def _build_search_request(
        self,
        *,
        query_vector: list[float] | None,
        query_text: str | None,
        top_k: int,
        filters: Sequence[FilterCondition],
        include_payload: bool,
        output_fields: Sequence[str],
        search_params: dict[str, Any] | None,
        sparse_search_params: dict[str, Any] | None,
        retrieval_mode: RetrievalMode,
        candidate_top_k: int | None,
        reranker: SearchReranker | None,
        consistency_level: ConsistencyLevel | None,
    ) -> SearchRequest:
        return SearchRequest(
            vector=query_vector,
            query_text=query_text,
            top_k=top_k,
            filters=[
                *self.build_scope_filters(),
                *self.build_default_search_filters(),
                *filters,
            ],
            include_payload=include_payload,
            output_fields=list(output_fields),
            search_params=dict(search_params or {}),
            sparse_search_params=dict(sparse_search_params or {}),
            retrieval_mode=retrieval_mode,
            candidate_top_k=candidate_top_k,
            reranker=reranker,
            consistency_level=consistency_level,
        )


def find_scalar_field(*, spec: CollectionSpec, field_name: str) -> ScalarFieldSpec | None:
    """按字段名查找 collection scalar field。"""
    return next((field for field in spec.scalar_fields if field.name == field_name), None)


def validate_scope_value_type(*, field: ScalarFieldSpec, value: ScopeValue) -> None:
    """校验 scope_value 类型与 scope_field 标量类型一致。"""
    if isinstance(value, bool):
        raise TypeError("scope_value 只支持 int 或 str，不支持 bool")

    if field.data_type == ScalarDataType.INT64:
        if not isinstance(value, int):
            raise TypeError(f"scope_field={field.name} 使用 INT64 时 scope_value 必须是 int")
        return

    if field.data_type == ScalarDataType.STRING:
        if not isinstance(value, str):
            raise TypeError(f"scope_field={field.name} 使用 STRING 时 scope_value 必须是 str")
        return

    raise ValueError(f"scope_field={field.name} 只支持 INT64 或 STRING，不支持 {field.data_type.value}")


def as_scalar_list(values: list[Any]) -> list[ScalarValue]:
    """将任意列表转换为标量值列表（类型断言辅助函数）。"""
    return [cast(ScalarValue, value) for value in values]


def build_scalar_filters(
    filters: dict[str, Any] | None = None,
) -> tuple[FilterCondition, ...]:
    """从字典构建过滤条件列表。

    转换规则：
    - None 值: 跳过
    - 列表值: 转换为 IN 操作符
    - 单值: 转换为 EQ 操作符

    示例:
        {"org_id": 1, "status": ["active", "pending"], "deleted": None}
        -> (FilterCondition(org_id, EQ, 1), FilterCondition(status, IN, [...]))
    """
    if not filters:
        return ()

    conditions: list[FilterCondition] = []
    for key, value in filters.items():
        if value is None:
            continue
        if isinstance(value, list):
            if not value:
                continue
            conditions.append(
                FilterCondition(
                    field=key,
                    op=FilterOperator.IN,
                    value=as_scalar_list(value),
                )
            )
            continue
        conditions.append(FilterCondition(field=key, op=FilterOperator.EQ, value=value))
    return tuple(conditions)


def numeric_id_sort_key(value: Any) -> tuple[int, str]:
    """数字优先的排序键函数。

    用于对 ID 进行排序，纯数字 ID 按数值排序，非数字 ID 按字符串排序。
    返回元组 (优先级, 排序键)，数字优先级为 0，非数字为 1。

    示例: ["2", "10", "1", "a"] -> ["1", "2", "10", "a"]
    """
    text = str(value)
    if text.isdigit():
        return 0, f"{int(text):020d}"
    return 1, text
