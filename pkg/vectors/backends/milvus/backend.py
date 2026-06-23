from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast

import anyio
import grpc
from pymilvus import AnnSearchRequest, MilvusClient, RRFRanker, WeightedRanker
from pymilvus.exceptions import (
    ConnectError,
    ConnectionNotExistException,
    MilvusUnavailableException,
)

from pkg.concurrency import anyio_run_in_thread
from pkg.vectors.backends.base import BaseVectorBackend, CollectionSpec
from pkg.vectors.contracts import (
    FilterCondition,
    RerankerStrategy,
    RetrievalMode,
    SearchHit,
    SearchRequest,
    VectorRecord,
)

from .codec import (
    build_fetch_output_fields,
    build_filter_expression,
    build_search_output_fields,
    hit_to_search_hit,
    record_to_row,
    row_to_record,
)
from .schema import (
    build_index_params,
    build_schema,
    map_metric_type,
    validate_collection_description,
)
from .types import (
    DEFAULT_HNSW_SEARCH_EF,
    MilvusCollectionSpec,
    MilvusDenseIndexType,
    SearchBranchPlan,
    SearchExecutionPlan,
)

logger = logging.getLogger(__name__)


class MilvusBackend(BaseVectorBackend):
    """基于 pymilvus.MilvusClient 的 clean backend。"""

    def __init__(
        self,
        *,
        uri: str,
        token: str | None = None,
        db_name: str | None = None,
        timeout: float | None = None,
        default_search_params: dict[str, object] | None = None,
    ) -> None:
        self._uri = uri
        self._token = token or ""
        self._db_name = db_name or ""
        self._timeout = timeout
        self._default_search_params = default_search_params or {}
        self._client: MilvusClient | None = None
        self._client_guard = threading.Lock()
        self._recovery_lock = anyio.Lock()
        self._collection_locks: dict[str, anyio.Lock] = {}
        self._loaded_collections: set[str] = set()
        self._ensured_collections: set[str] = set()
        self._is_shutdown = False

    # ------------------------------------------------------------------
    # Lifecycle and client access
    # ------------------------------------------------------------------

    @property
    def client(self) -> MilvusClient:
        if self._client is not None:
            return self._client

        if self._is_shutdown:
            raise RuntimeError("Milvus backend is shut down")

        with self._client_guard:
            if self._client is None:
                if self._is_shutdown:
                    raise RuntimeError("Milvus backend is shut down")
                self._client = MilvusClient(
                    uri=self._uri,
                    token=self._token,
                    db_name=self._db_name,
                    timeout=self._timeout,
                )
            return self._client

    def close(self) -> None:
        self._close_client(reset_only=True)

    def shutdown(self) -> None:
        self._close_client(reset_only=False)

    def _close_client(self, *, reset_only: bool) -> None:
        with self._client_guard:
            client = self._client
            self._client = None
            if not reset_only:
                self._is_shutdown = True
            self._loaded_collections.clear()
            self._ensured_collections.clear()

        if client is not None:
            with contextlib.suppress(Exception):
                client.close()

    async def _call_client[T](
        self,
        operation: Callable[[MilvusClient], T],
    ) -> T:
        def _run_operation() -> T:
            return operation(self.client)

        return await anyio_run_in_thread(_run_operation)

    async def _call_client_method(self, method_name: str, /, **kwargs: object) -> object:
        def _run_method(client: MilvusClient) -> object:
            method = getattr(client, method_name)
            return method(**kwargs)

        return await self._call_client(_run_method)

    async def _run_with_recovery[T](
        self,
        operation_name: str,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        try:
            return await operation()
        except Exception as exc:
            if not self._is_recoverable_client_error(exc):
                raise
            logger.warning(
                "milvus operation %s hit recoverable error, resetting client: %s",
                operation_name,
                type(exc).__name__,
            )
            await self._reset_client_state()
            return await operation()

    async def _reset_client_state(
        self, *, stale_client: MilvusClient | None = None
    ) -> None:
        async with self._recovery_lock:
            client = self._client
            if stale_client is not None and client is not stale_client:
                return

            self._client = None
            self._loaded_collections.clear()
            self._ensured_collections.clear()

            if client is None:
                return

            with contextlib.suppress(Exception):
                await anyio_run_in_thread(client.close)

    @staticmethod
    def _is_recoverable_client_error(exc: Exception) -> bool:
        if isinstance(
            exc,
            (ConnectError, ConnectionNotExistException, MilvusUnavailableException),
        ):
            return True
        if isinstance(exc, grpc.RpcError):
            return exc.code() in {
                grpc.StatusCode.UNAVAILABLE,
                grpc.StatusCode.DEADLINE_EXCEEDED,
                grpc.StatusCode.CANCELLED,
            }
        return False

    @staticmethod
    def _require_spec(*, spec: CollectionSpec) -> MilvusCollectionSpec:
        if not isinstance(spec, MilvusCollectionSpec):
            raise TypeError(
                f"Milvus backend 需要 MilvusCollectionSpec，实际收到: {type(spec).__name__}"
            )
        return spec

    # ------------------------------------------------------------------
    # Collection lifecycle
    # ------------------------------------------------------------------

    async def ensure_collection(self, *, spec: CollectionSpec) -> None:
        spec = self._require_spec(spec=spec)

        async def _run() -> None:
            if spec.name in self._ensured_collections:
                return

            async with self._get_collection_lock(collection_name=spec.name):
                if spec.name in self._ensured_collections:
                    return

                exists = spec.name in self._loaded_collections
                if not exists:
                    exists = bool(
                        await self._call_client_method(
                            "has_collection",
                            collection_name=spec.name,
                        )
                    )

                if exists:
                    await self._validate_existing_collection(spec=spec)
                    await self._load_collection_if_needed(collection_name=spec.name)
                    logger.debug(
                        "milvus collection validated and loaded: %s", spec.name
                    )
                else:
                    schema = build_schema(spec=spec)
                    index_params = build_index_params(client=self.client, spec=spec)
                    await self._call_client_method(
                        "create_collection",
                        collection_name=spec.name,
                        schema=schema,
                        index_params=index_params,
                        consistency_level=spec.consistency_level.value,
                    )
                    await self._load_collection_if_needed(collection_name=spec.name)
                    logger.info("milvus collection created: %s", spec.name)

                self._ensured_collections.add(spec.name)

        await self._run_with_recovery("ensure_collection", _run)

    async def load_collection(self, *, collection_name: str) -> None:
        async def _run() -> None:
            async with self._get_collection_lock(collection_name=collection_name):
                await self._load_collection_if_needed(collection_name=collection_name)

        await self._run_with_recovery("load_collection", _run)

    async def release_collection(self, *, collection_name: str) -> None:
        async def _run() -> None:
            async with self._get_collection_lock(collection_name=collection_name):
                await self._call_client_method(
                    "release_collection",
                    collection_name=collection_name,
                )
                self._loaded_collections.discard(collection_name)
                self._ensured_collections.discard(collection_name)
                logger.debug("milvus collection released: %s", collection_name)

        await self._run_with_recovery("release_collection", _run)

    async def _load_collection_if_exists(self, *, collection_name: str) -> bool:
        if collection_name in self._loaded_collections:
            return True

        async with self._get_collection_lock(collection_name=collection_name):
            if collection_name in self._loaded_collections:
                return True

            exists = await self._collection_exists(collection_name=collection_name)
            if not exists:
                return False

            await self._load_collection_if_needed(collection_name=collection_name)
            return True

    def _get_collection_lock(self, *, collection_name: str) -> anyio.Lock:
        lock = self._collection_locks.get(collection_name)
        if lock is None:
            lock = anyio.Lock()
            self._collection_locks[collection_name] = lock
        return lock

    async def _load_collection_if_needed(self, *, collection_name: str) -> None:
        if collection_name in self._loaded_collections:
            return

        await self._call_client_method(
            "load_collection",
            collection_name=collection_name,
        )
        self._loaded_collections.add(collection_name)
        logger.debug("milvus collection loaded: %s", collection_name)

    async def _collection_exists(self, *, collection_name: str) -> bool:
        if collection_name in self._loaded_collections:
            return True

        return bool(
            await self._call_client_method(
                "has_collection",
                collection_name=collection_name,
            )
        )

    async def _validate_existing_collection(self, *, spec: CollectionSpec) -> None:
        description = await self._call_client_method(
            "describe_collection",
            collection_name=spec.name,
        )
        validate_collection_description(description=description, spec=spec)

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------

    async def upsert(
        self, *, spec: CollectionSpec, records: Sequence[VectorRecord]
    ) -> None:
        spec = self._require_spec(spec=spec)

        async def _run() -> None:
            if not records:
                return

            self.validate_records(spec=spec, records=records)
            await self.ensure_collection(spec=spec)
            rows = [record_to_row(spec=spec, record=record) for record in records]
            await self._call_client_method(
                "upsert",
                collection_name=spec.name,
                data=rows,
            )

        await self._run_with_recovery("upsert", _run)

    async def delete(
        self,
        *,
        spec: CollectionSpec,
        ids: Sequence[int] | None = None,
        filters: Sequence[FilterCondition] | None = None,
    ) -> int:
        spec = self._require_spec(spec=spec)

        async def _run() -> int:
            if not await self._collection_exists(collection_name=spec.name):
                return 0

            expr = build_filter_expression(spec=spec, ids=ids, filters=filters)
            if ids is not None and len(ids) > 0 and not filters:
                result = cast(
                    dict[str, object],
                    await self._call_client_method(
                        "delete",
                        collection_name=spec.name,
                        ids=list(ids),
                    ),
                )
            elif expr:
                result = cast(
                    dict[str, object],
                    await self._call_client_method(
                        "delete",
                        collection_name=spec.name,
                        filter=expr,
                    ),
                )
            else:
                return 0

            delete_count = result.get("delete_count", 0)
            if isinstance(delete_count, bool):
                return int(delete_count)
            if isinstance(delete_count, (int, float, str)):
                return int(delete_count)
            return 0

        return await self._run_with_recovery("delete", _run)

    async def fetch(
        self,
        *,
        spec: CollectionSpec,
        ids: Sequence[int] | None = None,
        filters: Sequence[FilterCondition] | None = None,
        limit: int | None = None,
        consistency_level: object | None = None,
    ) -> list[VectorRecord]:
        spec = self._require_spec(spec=spec)

        async def _run() -> list[VectorRecord]:
            if not await self._load_collection_if_exists(collection_name=spec.name):
                return []
            if not ids and not filters:
                raise ValueError(
                    "fetch 需要至少提供 ids 或 filters，禁止无条件整表扫描"
                )

            output_fields = build_fetch_output_fields(spec=spec)
            expr = build_filter_expression(spec=spec, ids=ids, filters=filters)
            resolved_consistency_level = self._resolve_consistency_level(
                spec=spec,
                override=consistency_level,
            )
            if ids is not None and len(ids) > 0 and not filters:
                rows = cast(
                    list[dict[str, Any]],
                    await self._call_client_method(
                        "query",
                        collection_name=spec.name,
                        ids=list(ids),
                        output_fields=output_fields,
                        consistency_level=resolved_consistency_level,
                    ),
                )
            else:
                query_kwargs: dict[str, object] = {}
                if limit is not None:
                    query_kwargs["limit"] = limit
                rows = cast(
                    list[dict[str, Any]],
                    await self._call_client_method(
                        "query",
                        collection_name=spec.name,
                        filter=expr,
                        output_fields=output_fields,
                        consistency_level=resolved_consistency_level,
                        **query_kwargs,
                    ),
                )

            records = [row_to_record(spec=spec, row=row) for row in rows]
            return records[:limit] if limit is not None else records

        return await self._run_with_recovery("fetch", _run)

    async def healthcheck(self) -> dict[str, str]:
        async def _run() -> dict[str, str]:
            version = await self._call_client_method("get_server_version")
            return {
                "backend": "milvus",
                "status": "ok",
                "version": str(version),
            }

        return await self._run_with_recovery("healthcheck", _run)

    async def search(
        self, *, spec: CollectionSpec, request: SearchRequest
    ) -> list[SearchHit]:
        spec = self._require_spec(spec=spec)

        async def _run() -> list[SearchHit]:
            self.validate_search_request(spec=spec, request=request)
            if not await self._load_collection_if_exists(collection_name=spec.name):
                return []

            expr = build_filter_expression(
                spec=spec,
                ids=None,
                filters=request.filters,
            )
            output_fields = build_search_output_fields(spec=spec, request=request)
            plan = self._build_search_plan(
                spec=spec,
                request=request,
                expr=expr,
                output_fields=output_fields,
            )
            normalize_relevance = plan.mode == RetrievalMode.DENSE
            start_time = time.perf_counter()
            logger.debug(
                "milvus search start collection=%s mode=%s branches=%s top_k=%d candidate_top_k=%s",
                spec.name,
                plan.mode.value,
                ",".join(plan.branch_names),
                plan.final_limit,
                plan.candidate_limit,
            )
            raw_results = await self._execute_search_plan(spec=spec, plan=plan)

            hits = raw_results[0] if raw_results else []
            search_hits = [
                hit_to_search_hit(
                    spec=spec,
                    hit=hit,
                    retrieval_mode=plan.mode,
                    normalize_relevance=normalize_relevance,
                )
                for hit in hits
            ]
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.debug(
                "milvus search complete collection=%s mode=%s hits=%d duration_ms=%.2f",
                spec.name,
                plan.mode.value,
                len(search_hits),
                duration_ms,
            )
            return search_hits

        return await self._run_with_recovery("search", _run)

    # ------------------------------------------------------------------
    # Search planning and execution
    # ------------------------------------------------------------------

    def _build_search_params(
        self,
        *,
        spec: MilvusCollectionSpec,
        request: SearchRequest,
        limit: int,
    ) -> dict[str, object]:
        default_search_params = dict(self._default_search_params)
        request_search_params = dict(request.search_params)

        default_params = default_search_params.pop("params", {})
        request_has_params = "params" in request_search_params
        request_params = request_search_params.pop("params", {})

        final_search_params: dict[str, Any] = {
            "metric_type": spec.metric_type.value.upper(),
            **default_search_params,
            **request_search_params,
        }

        if isinstance(default_params, dict) and isinstance(request_params, dict):
            params: object = {**default_params, **request_params}
        else:
            params = request_params if request_has_params else default_params
        final_search_params["params"] = params

        self._set_default_hnsw_ef(spec=spec, search_params=final_search_params, limit=limit)
        return final_search_params

    def _set_default_hnsw_ef(
        self,
        *,
        spec: MilvusCollectionSpec,
        search_params: dict[str, object],
        limit: int,
    ) -> None:
        index_type = str(spec.index_config.index_type).upper()
        params = search_params.get("params")
        if index_type != MilvusDenseIndexType.HNSW.value or not isinstance(params, dict) or "ef" in params:
            return
        params["ef"] = max(DEFAULT_HNSW_SEARCH_EF, limit)

    def _build_search_plan(
        self,
        *,
        spec: MilvusCollectionSpec,
        request: SearchRequest,
        expr: str,
        output_fields: list[str],
    ) -> SearchExecutionPlan:
        mode = self._resolve_retrieval_mode(spec=spec, request=request)
        consistency_level = self._resolve_consistency_level(
            spec=spec,
            override=request.consistency_level,
        )

        if mode == RetrievalMode.DENSE:
            if request.vector is None:
                raise ValueError("dense 检索需要 query vector")
            return SearchExecutionPlan(
                mode=mode,
                expr=expr,
                output_fields=tuple(output_fields),
                consistency_level=consistency_level,
                final_limit=request.top_k,
                branches=(
                    SearchBranchPlan(
                        name=RetrievalMode.DENSE.value,
                        anns_field=spec.vector_field,
                        data=[request.vector],
                        search_params=self._build_search_params(
                            spec=spec, request=request, limit=request.top_k
                        ),
                        limit=request.top_k,
                    ),
                ),
            )

        if mode == RetrievalMode.FULL_TEXT:
            if request.query_text is None:
                raise ValueError("full-text 检索需要 query_text")
            return SearchExecutionPlan(
                mode=mode,
                expr=expr,
                output_fields=tuple(output_fields),
                consistency_level=consistency_level,
                final_limit=request.top_k,
                branches=(
                    SearchBranchPlan(
                        name=RetrievalMode.FULL_TEXT.value,
                        anns_field=spec.full_text_search.sparse_vector_field,
                        data=[request.query_text],
                        search_params=self._build_sparse_search_params(request=request),
                        limit=request.top_k,
                    ),
                ),
            )

        if request.vector is None:
            raise ValueError("hybrid 检索需要 query vector")
        if request.query_text is None:
            raise ValueError("hybrid 检索需要 query_text")
        candidate_limit = request.candidate_top_k or request.top_k
        branches = (
            SearchBranchPlan(
                name=RetrievalMode.DENSE.value,
                anns_field=spec.vector_field,
                data=[request.vector],
                search_params=self._build_search_params(spec=spec, request=request, limit=candidate_limit),
                limit=candidate_limit,
            ),
            SearchBranchPlan(
                name=RetrievalMode.FULL_TEXT.value,
                anns_field=spec.full_text_search.sparse_vector_field,
                data=[request.query_text],
                search_params=self._build_sparse_search_params(request=request),
                limit=candidate_limit,
            ),
        )
        return SearchExecutionPlan(
            mode=mode,
            expr=expr,
            output_fields=tuple(output_fields),
            consistency_level=consistency_level,
            final_limit=request.top_k,
            branches=branches,
            candidate_limit=candidate_limit,
            ranker=self._build_reranker(request=request, request_count=len(branches)),
        )

    async def _execute_search_plan(
        self,
        *,
        spec: MilvusCollectionSpec,
        plan: SearchExecutionPlan,
    ) -> list[list[dict[str, Any]]]:
        if plan.mode == RetrievalMode.HYBRID:
            reqs = [
                AnnSearchRequest(
                    data=branch.data,
                    anns_field=branch.anns_field,
                    param=branch.search_params,
                    limit=branch.limit,
                    expr=plan.expr or None,
                )
                for branch in plan.branches
            ]
            return cast(
                list[list[dict[str, Any]]],
                await self._call_client_method(
                    "hybrid_search",
                    collection_name=spec.name,
                    reqs=reqs,
                    ranker=plan.ranker,
                    limit=plan.final_limit,
                    output_fields=list(plan.output_fields),
                    consistency_level=plan.consistency_level,
                ),
            )

        branch = plan.branches[0]
        return cast(
            list[list[dict[str, Any]]],
            await self._call_client_method(
                "search",
                collection_name=spec.name,
                data=branch.data,
                filter=plan.expr,
                limit=plan.final_limit,
                output_fields=list(plan.output_fields),
                search_params=branch.search_params,
                anns_field=branch.anns_field,
                consistency_level=plan.consistency_level,
            ),
        )

    def _build_sparse_search_params(
        self,
        *,
        request: SearchRequest,
    ) -> dict[str, object]:
        search_params: dict[str, object] = {
            "metric_type": "BM25",
            "params": {},
        }
        search_params.update(request.sparse_search_params)
        return search_params

    def _build_reranker(
        self,
        *,
        request: SearchRequest,
        request_count: int,
    ) -> RRFRanker | WeightedRanker:
        reranker = request.reranker
        if reranker is None:
            return RRFRanker()

        if reranker.strategy == RerankerStrategy.RRF:
            return RRFRanker(reranker.k)

        if len(reranker.weights) != request_count:
            raise ValueError(
                f"Weighted reranker 权重数量不匹配: got={len(reranker.weights)}, expected={request_count}"
            )
        return WeightedRanker(*reranker.weights, norm_score=reranker.normalize_score)

    def _resolve_retrieval_mode(
        self,
        *,
        spec: MilvusCollectionSpec,
        request: SearchRequest,
    ) -> RetrievalMode:
        if request.retrieval_mode == RetrievalMode.AUTO:
            if request.vector is not None and request.query_text:
                if not spec.full_text_search.enabled:
                    raise ValueError(
                        "AUTO 模式收到 vector + query_text，但当前 collection 未启用 BM25/full-text search；"
                        "请显式改用 DENSE，或先为 collection 开启 full-text"
                    )
                return RetrievalMode.HYBRID
            if request.vector is not None:
                return RetrievalMode.DENSE
            if request.query_text:
                if not spec.full_text_search.enabled:
                    raise ValueError(
                        "query_text 检索要求 collection 启用 BM25/full-text search"
                    )
                return RetrievalMode.FULL_TEXT
            raise ValueError("至少需要提供 query vector 或 query_text")

        if (
            request.retrieval_mode in {RetrievalMode.FULL_TEXT, RetrievalMode.HYBRID}
            and not spec.full_text_search.enabled
        ):
            raise ValueError("当前 collection 未启用 BM25/full-text search")
        return request.retrieval_mode

    @staticmethod
    def _resolve_consistency_level(
        *,
        spec: MilvusCollectionSpec,
        override: object | None,
    ) -> str:
        resolved = override if override is not None else spec.consistency_level
        value = getattr(resolved, "value", resolved)
        return str(value)
