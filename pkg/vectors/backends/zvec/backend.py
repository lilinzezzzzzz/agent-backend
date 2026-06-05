from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from pathlib import Path

import anyio
import zvec
from zvec import Collection, CollectionOption

from pkg.concurrency import anyio_run_in_thread
from pkg.vectors.backends.base import BaseVectorBackend, CollectionSpec
from pkg.vectors.contracts import FilterCondition, SearchHit, SearchRequest, VectorRecord
from pkg.vectors.errors import CapabilityNotSupportedError

from .codec import (
    build_filter_expression,
    doc_to_record,
    doc_to_search_hit,
    extract_delete_count,
    record_to_doc,
)
from .schema import build_schema


class ZvecBackend(BaseVectorBackend):
    """基于 zvec 的向量 backend 实现。"""

    def __init__(
        self,
        *,
        root_path: str,
        read_only: bool = False,
        enable_mmap: bool = True,
    ) -> None:
        self._root_path = Path(root_path).expanduser().resolve()
        self._read_only = read_only
        self._enable_mmap = enable_mmap

        self._collections: dict[str, Collection] = {}
        self._collection_locks: dict[str, anyio.Lock] = {}
        self._thread_locks: dict[str, threading.Lock] = {}
        self._ensured_collections: set[str] = set()
        self._is_shutdown = False

    async def ensure_collection(self, *, spec: CollectionSpec) -> None:
        self._ensure_not_shutdown()
        if spec.name in self._ensured_collections:
            return

        async with self._get_collection_lock(collection_name=spec.name):
            if spec.name in self._ensured_collections:
                return

            collection = await self._open_or_create_collection(spec=spec)
            self._collections[spec.name] = collection
            self._ensured_collections.add(spec.name)

    async def upsert(
        self,
        *,
        spec: CollectionSpec,
        records: Sequence[VectorRecord],
    ) -> None:
        self._ensure_not_shutdown()
        if not records:
            return

        self.validate_records(spec=spec, records=records)
        await self.ensure_collection(spec=spec)
        docs = [record_to_doc(spec=spec, record=record) for record in records]
        await self._call_collection(
            collection_name=spec.name,
            operation=lambda c: c.upsert(docs),
        )

    async def delete(
        self,
        *,
        spec: CollectionSpec,
        ids: Sequence[int] | None = None,
        filters: Sequence[FilterCondition] | None = None,
    ) -> int:
        self._ensure_not_shutdown()
        if (not ids) and (not filters):
            return 0

        collection = await self._get_collection_if_exists(collection_name=spec.name)
        if collection is None:
            return 0

        if ids and not filters:
            result = await self._call_collection(
                collection_name=spec.name,
                operation=lambda c: c.delete([str(item) for item in ids]),
            )
            return extract_delete_count(result=result, fallback=len(ids))

        expression = build_filter_expression(spec=spec, ids=ids, filters=filters)
        result = await self._call_collection(
            collection_name=spec.name,
            operation=lambda c: c.delete_by_filter(filter=expression),
        )
        return extract_delete_count(result=result, fallback=0)

    async def fetch(
        self,
        *,
        spec: CollectionSpec,
        ids: Sequence[int] | None = None,
        filters: Sequence[FilterCondition] | None = None,
        limit: int | None = None,
        consistency_level: object | None = None,
    ) -> list[VectorRecord]:
        self._ensure_not_shutdown()
        if filters:
            raise CapabilityNotSupportedError("zvec backend 暂不支持 fetch(filters=...)")
        if not ids:
            return []

        collection = await self._get_collection_if_exists(collection_name=spec.name)
        if collection is None:
            return []

        docs = await self._call_collection(
            collection_name=spec.name,
            operation=lambda c: c.fetch([str(item) for item in ids]),
        )
        records = [doc_to_record(spec=spec, doc=doc) for doc in docs]
        return records[:limit] if limit is not None else records

    async def search(
        self,
        *,
        spec: CollectionSpec,
        request: SearchRequest,
    ) -> list[SearchHit]:
        self._ensure_not_shutdown()
        self.validate_search_request(spec=spec, request=request)
        if request.vector is None:
            raise CapabilityNotSupportedError("zvec backend 仅支持 dense vector 检索")

        collection = await self._get_collection_if_exists(collection_name=spec.name)
        if collection is None:
            return []

        expression = build_filter_expression(
            spec=spec,
            ids=None,
            filters=request.filters,
        )
        vector_query = zvec.VectorQuery(
            field_name=spec.vector_field,
            vector=request.vector,
        )
        docs = await self._call_collection(
            collection_name=spec.name,
            operation=lambda c: c.query(
                vector_query,
                filter=expression or None,
                topk=request.top_k,
            ),
        )
        return [
            doc_to_search_hit(
                spec=spec,
                doc=doc,
                include_payload=request.include_payload,
            )
            for doc in docs
        ]

    async def healthcheck(self) -> dict[str, str]:
        self._ensure_not_shutdown()
        version = getattr(zvec, "__version__", "unknown")
        return {
            "backend": "zvec",
            "status": "ok",
            "version": str(version),
        }

    def shutdown(self) -> None:
        self._collections.clear()
        self._ensured_collections.clear()
        self._is_shutdown = True

    def close(self) -> None:
        self._collections.clear()
        self._ensured_collections.clear()

    async def _call_collection[T](
        self,
        *,
        collection_name: str,
        operation: Callable[[Collection], T],
    ) -> T:
        collection = await self._get_collection_or_raise(collection_name=collection_name)
        thread_lock = self._get_thread_lock(collection_name=collection_name)

        def _run() -> T:
            with thread_lock:
                return operation(collection)

        return await anyio_run_in_thread(_run)

    async def _open_or_create_collection(self, *, spec: CollectionSpec) -> Collection:
        collection_path = self._collection_path(collection_name=spec.name)
        options = CollectionOption(
            read_only=self._read_only,
            enable_mmap=self._enable_mmap,
        )

        def _run() -> Collection:
            try:
                return zvec.open(collection_path, options)
            except Exception:
                schema = build_schema(spec=spec)
                return zvec.create_and_open(
                    path=collection_path,
                    schema=schema,
                    option=options,
                )

        return await anyio_run_in_thread(_run)

    async def _open_collection(self, *, collection_name: str) -> Collection | None:
        collection_path = self._collection_path(collection_name=collection_name)
        options = CollectionOption(
            read_only=self._read_only,
            enable_mmap=self._enable_mmap,
        )

        def _run() -> Collection:
            return zvec.open(collection_path, options)

        try:
            return await anyio_run_in_thread(_run)
        except Exception:
            return None

    async def _get_collection_or_raise(self, *, collection_name: str) -> Collection:
        collection = await self._get_collection_if_exists(collection_name=collection_name)
        if collection is None:
            raise ValueError(f"collection not found: {collection_name}")
        return collection

    async def _get_collection_if_exists(self, *, collection_name: str) -> Collection | None:
        collection = self._collections.get(collection_name)
        if collection is not None:
            return collection

        async with self._get_collection_lock(collection_name=collection_name):
            collection = self._collections.get(collection_name)
            if collection is not None:
                return collection

            opened = await self._open_collection(collection_name=collection_name)
            if opened is None:
                return None
            self._collections[collection_name] = opened
            return opened

    def _get_collection_lock(self, *, collection_name: str) -> anyio.Lock:
        lock = self._collection_locks.get(collection_name)
        if lock is None:
            lock = anyio.Lock()
            self._collection_locks[collection_name] = lock
        return lock

    def _get_thread_lock(self, *, collection_name: str) -> threading.Lock:
        lock = self._thread_locks.get(collection_name)
        if lock is None:
            lock = threading.Lock()
            self._thread_locks[collection_name] = lock
        return lock

    def _collection_path(self, *, collection_name: str) -> str:
        return str(self._root_path / collection_name)

    def _ensure_not_shutdown(self) -> None:
        if self._is_shutdown:
            raise RuntimeError("Zvec backend is shut down")
