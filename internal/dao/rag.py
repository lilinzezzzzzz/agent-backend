from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RagChunkMetadata:
    chunk_id: int
    doc_id: int | None = None
    kb_id: int | None = None
    title: str | None = None
    source_uri: str | None = None
    document_version: str | None = None


class RagMetadataDao:
    """RAG metadata access boundary.

    首版仓库还没有知识库、文档和 chunk ORM 表，因此默认实现返回空映射。
    Service 会批量调用本 DAO，并从 vector hit metadata/payload 做兼容补齐；
    后续接入真实 registry 时只需要替换这里的批量查询实现。
    """

    async def get_chunk_metadata_map(
        self,
        *,
        chunk_ids: Sequence[int],
    ) -> dict[int, RagChunkMetadata]:
        if not chunk_ids:
            return {}
        return {}


_rag_metadata_dao: RagMetadataDao | None = None


def new_rag_metadata_dao() -> RagMetadataDao:
    global _rag_metadata_dao
    if _rag_metadata_dao is None:
        _rag_metadata_dao = RagMetadataDao()
    return _rag_metadata_dao
