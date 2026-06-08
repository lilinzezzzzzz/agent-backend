"""把后处理后的检索结果组装成可直接放入 LLM prompt 的上下文。

本模块位于 RAG 检索链路的最后一段：

1. `BaseVectorRepository.search_*()` 从向量后端返回原始 `SearchHit`。
2. `PostRetrievalPipeline` 负责 dedup、rerank 和按文档 collapse。
3. `DocumentContextAssembler` 接收 `CollapsedSearchHit` / `PostRetrievalResult`，
   按文档标题、来源、chunk 顺序、相邻窗口和字符预算拼出最终 `context_text`。

这里不负责召回、重排、补邻近 chunk 或访问外部存储；调用方必须在传入前准备好需要
喂给 LLM 的 chunk 集合。仓储层的接入入口是
`BaseVectorRepository.assemble_context_by_text()` 和
`BaseVectorRepository.assemble_context_by_vector()`。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from pkg.vectors.contracts import RetrievalMode, ScalarValue, SearchHit
from pkg.vectors.post_retrieval import CollapsedSearchHit, PostRetrievalResult


class ContextHeaderConfig(BaseModel, extra="forbid"):
    """控制 document/window header 的来源字段和输出行为。"""

    enabled: bool = True
    include_document_key: bool = True
    include_retrieval_mode: bool = False
    title_fields: list[str] = Field(
        default_factory=lambda: [
            "metadata.title",
            "payload.title",
            "metadata.document_title",
            "payload.document_title",
            "metadata.source_title",
            "payload.source_title",
            "metadata.filename",
            "payload.filename",
        ]
    )
    source_fields: list[str] = Field(
        default_factory=lambda: [
            "metadata.source",
            "payload.source",
            "metadata.url",
            "payload.url",
            "metadata.source_url",
            "payload.source_url",
            "metadata.path",
            "payload.path",
        ]
    )
    section_fields: list[str] = Field(
        default_factory=lambda: [
            "metadata.section",
            "payload.section",
            "metadata.heading",
            "payload.heading",
            "metadata.section_title",
            "payload.section_title",
        ]
    )
    page_fields: list[str] = Field(
        default_factory=lambda: [
            "metadata.page",
            "payload.page",
            "metadata.page_number",
            "payload.page_number",
        ]
    )


class ContextWindowConfig(BaseModel, extra="forbid"):
    """控制 chunk 排序、window 合并和 window 文本拼装。"""

    order_fields: list[str] = Field(
        default_factory=lambda: [
            "metadata.chunk_index",
            "payload.chunk_index",
            "metadata.position",
            "payload.position",
            "metadata.order",
            "payload.order",
            "metadata.start_offset",
            "payload.start_offset",
            "id",
        ]
    )
    merge_adjacent_chunks: bool = True
    adjacent_order_gap: int = Field(default=1, ge=0)
    max_chunks_per_window: int = Field(default=3, gt=0)
    chunk_separator: str = "\n\n"
    window_separator: str = "\n\n"
    include_headers: bool = False


class ContextBudgetConfig(BaseModel, extra="forbid"):
    """控制返回给 LLM 的 document 数量和字符预算。"""

    max_documents: int | None = Field(default=None, gt=0)
    max_total_chars: int | None = Field(default=None, gt=0)
    max_document_chars: int | None = Field(default=None, gt=0)


class ContextAssemblyConfig(BaseModel, extra="forbid"):
    """document context assembly 总配置。"""

    header: ContextHeaderConfig = Field(default_factory=ContextHeaderConfig)
    window: ContextWindowConfig = Field(default_factory=ContextWindowConfig)
    budget: ContextBudgetConfig = Field(default_factory=ContextBudgetConfig)
    document_separator: str = "\n\n---\n\n"
    header_body_separator: str = "\n\n"
    normalize_whitespace: bool = True
    drop_empty_chunks: bool = True
    ellipsis: str = "\n...[truncated]"


class AssembledContextChunk(BaseModel, extra="forbid"):
    chunk_id: int = Field(gt=0)
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    order_field: str | None = None
    order_value: ScalarValue | None = None


class AssembledContextWindow(BaseModel, extra="forbid"):
    window_index: int = Field(ge=1)
    chunk_ids: list[int] = Field(default_factory=list)
    text: str
    header: str | None = None
    section: str | None = None
    page: ScalarValue | None = None
    truncated: bool = False
    chunks: list[AssembledContextChunk] = Field(default_factory=list)


class AssembledDocumentContext(BaseModel, extra="forbid"):
    document_key: ScalarValue | str
    text: str
    header: str | None = None
    title: str | None = None
    source: str | None = None
    retrieval_mode: RetrievalMode | None = None
    relevance_score: float | None = None
    raw_score: float | None = None
    chunk_ids: list[int] = Field(default_factory=list)
    windows: list[AssembledContextWindow] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = False


class ContextAssemblyStats(BaseModel, extra="forbid"):
    input_document_count: int = Field(ge=0)
    returned_document_count: int = Field(ge=0)
    input_chunk_count: int = Field(ge=0)
    returned_chunk_count: int = Field(ge=0)
    window_count: int = Field(ge=0)
    truncated_document_count: int = Field(ge=0)
    truncated_window_count: int = Field(ge=0)


class ContextAssemblyResult(BaseModel, extra="forbid"):
    documents: list[AssembledDocumentContext] = Field(default_factory=list)
    context_text: str = ""
    stats: ContextAssemblyStats


@dataclass(slots=True)
class _ResolvedChunk:
    hit: SearchHit
    normalized_text: str
    order_field: str | None
    order_value: ScalarValue | None
    sort_group: int
    sort_value: float | str | int
    original_index: int


class DocumentContextAssembler:
    """把 collapse 后的 document/chunk 结果拼装成适合直接喂给 LLM 的上下文。

    接入方式：
    - 业务代码通常不直接 new 后手动传 documents，而是通过 repository 的
      `assemble_context_by_text()` / `assemble_context_by_vector()` 间接调用。
    - 需要定制 header、window 合并、chunk 分隔符或字符预算时，构造自定义
      `ContextAssemblyConfig` 后作为 `context_assembler` 传给 repository。
    - 如果调用方已经自行完成检索和 post-retrieval，也可以直接调用 `assemble()`。
    """

    def __init__(self, *, config: ContextAssemblyConfig | None = None) -> None:
        self._config = config or ContextAssemblyConfig()

    @property
    def config(self) -> ContextAssemblyConfig:
        return self._config

    def assemble(
        self,
        *,
        result: PostRetrievalResult | None = None,
        documents: Sequence[CollapsedSearchHit] | None = None,
    ) -> ContextAssemblyResult:
        """Assemble collapsed retrieval results into structured document contexts."""
        if result is None and documents is None:
            raise ValueError("assemble 需要提供 result 或 documents")
        if result is not None and documents is not None:
            raise ValueError("assemble 只能提供 result 或 documents 之一")

        source_documents = list(result.documents if result is not None else documents or [])
        input_document_count = len(source_documents)
        input_chunk_count = sum(len(document.chunks) for document in source_documents)

        if self._config.budget.max_documents is not None:
            source_documents = source_documents[: self._config.budget.max_documents]

        assembled_documents: list[AssembledDocumentContext] = []
        context_parts: list[str] = []
        remaining_total_chars = self._config.budget.max_total_chars

        for index, document in enumerate(source_documents, start=1):
            separator = self._config.document_separator if context_parts else ""
            available_chars = self._resolve_document_budget(
                remaining_total_chars=remaining_total_chars,
                separator=separator,
            )
            if available_chars is not None and available_chars <= 0:
                break

            assembled = self._assemble_document(
                document=document,
                document_index=index,
                char_budget=available_chars,
            )
            if assembled is None:
                continue

            context_parts.append(f"{separator}{assembled.text}")
            assembled_documents.append(assembled)
            if remaining_total_chars is not None:
                remaining_total_chars -= len(separator) + len(assembled.text)
                if remaining_total_chars <= 0:
                    break

        return ContextAssemblyResult(
            documents=assembled_documents,
            context_text="".join(context_parts),
            stats=ContextAssemblyStats(
                input_document_count=input_document_count,
                returned_document_count=len(assembled_documents),
                input_chunk_count=input_chunk_count,
                returned_chunk_count=sum(len(document.chunk_ids) for document in assembled_documents),
                window_count=sum(len(document.windows) for document in assembled_documents),
                truncated_document_count=sum(1 for document in assembled_documents if document.truncated),
                truncated_window_count=sum(
                    1
                    for document in assembled_documents
                    for window in document.windows
                    if window.truncated
                ),
            ),
        )

    def _assemble_document(
        self,
        *,
        document: CollapsedSearchHit,
        document_index: int,
        char_budget: int | None,
    ) -> AssembledDocumentContext | None:
        windows = self._build_windows(document=document)
        title = self._coerce_text(self._resolve_document_value(document=document, field_paths=self._config.header.title_fields))
        source = self._coerce_text(
            self._resolve_document_value(document=document, field_paths=self._config.header.source_fields)
        )
        header = self._build_document_header(
            document=document,
            document_index=document_index,
            title=title,
            source=source,
        )

        if not header and not windows:
            return None

        text, selected_windows, truncated = self._fit_document_text(
            header=header,
            windows=windows,
            char_budget=char_budget,
        )
        if not text:
            return None

        return AssembledDocumentContext(
            document_key=document.document_key,
            text=text,
            header=header,
            title=title,
            source=source,
            retrieval_mode=document.retrieval_mode,
            relevance_score=document.relevance_score,
            raw_score=document.raw_score,
            chunk_ids=[chunk.chunk_id for window in selected_windows for chunk in window.chunks],
            windows=selected_windows,
            metadata=dict(document.metadata),
            payload=dict(document.payload),
            truncated=truncated,
        )

    def _build_windows(self, *, document: CollapsedSearchHit) -> list[AssembledContextWindow]:
        ordered_chunks = self._resolve_chunks(document=document)
        if not ordered_chunks:
            return []

        windows: list[list[_ResolvedChunk]] = []
        current_window: list[_ResolvedChunk] = []

        for chunk in ordered_chunks:
            if not current_window:
                current_window = [chunk]
                continue
            previous = current_window[-1]
            if self._should_merge_chunks(previous=previous, current=chunk, current_window=current_window):
                current_window.append(chunk)
                continue
            windows.append(current_window)
            current_window = [chunk]

        if current_window:
            windows.append(current_window)

        assembled_windows: list[AssembledContextWindow] = []
        for window_index, grouped_chunks in enumerate(windows, start=1):
            chunks = [
                AssembledContextChunk(
                    chunk_id=chunk.hit.id,
                    text=chunk.normalized_text,
                    metadata=dict(chunk.hit.metadata),
                    payload=dict(chunk.hit.payload),
                    order_field=chunk.order_field,
                    order_value=chunk.order_value,
                )
                for chunk in grouped_chunks
            ]
            section = self._coerce_text(
                self._resolve_window_value(chunks=grouped_chunks, field_paths=self._config.header.section_fields)
            )
            page = self._resolve_window_value(chunks=grouped_chunks, field_paths=self._config.header.page_fields)
            header = self._build_window_header(
                window_index=window_index,
                chunk_ids=[chunk.hit.id for chunk in grouped_chunks],
                section=section,
                page=page,
            )
            body = self._config.window.chunk_separator.join(chunk.normalized_text for chunk in grouped_chunks)
            text = body if header is None else f"{header}{self._config.header_body_separator}{body}"
            assembled_windows.append(
                AssembledContextWindow(
                    window_index=window_index,
                    chunk_ids=[chunk.chunk_id for chunk in chunks],
                    text=text,
                    header=header,
                    section=section,
                    page=page,
                    chunks=chunks,
                )
            )
        return assembled_windows

    def _fit_document_text(
        self,
        *,
        header: str | None,
        windows: list[AssembledContextWindow],
        char_budget: int | None,
    ) -> tuple[str, list[AssembledContextWindow], bool]:
        if char_budget is not None and char_budget <= 0:
            return "", [], False

        if header is not None and char_budget is not None and len(header) >= char_budget:
            return self._truncate_text(header, char_budget), [], True

        text_parts: list[str] = []
        selected_windows: list[AssembledContextWindow] = []
        current_length = 0
        truncated = False

        if header is not None:
            text_parts.append(header)
            current_length = len(header)

        for window in windows:
            separator = (
                self._config.header_body_separator
                if text_parts and len(selected_windows) == 0 and header is not None
                else self._config.window.window_separator if text_parts else ""
            )
            candidate_length = current_length + len(separator) + len(window.text)
            if char_budget is None or candidate_length <= char_budget:
                text_parts.append(f"{separator}{window.text}")
                selected_windows.append(window)
                current_length = candidate_length
                continue

            remaining = char_budget - current_length - len(separator)
            if remaining <= 0:
                truncated = True
                break

            truncated_text = self._truncate_text(window.text, remaining)
            if truncated_text:
                text_parts.append(f"{separator}{truncated_text}")
                selected_windows.append(window.model_copy(update={"text": truncated_text, "truncated": True}))
            truncated = True
            break

        return "".join(text_parts), selected_windows, truncated

    def _resolve_chunks(self, *, document: CollapsedSearchHit) -> list[_ResolvedChunk]:
        resolved_chunks: list[_ResolvedChunk] = []
        for index, hit in enumerate(document.chunks):
            text = self._normalize_text(hit.text)
            if self._config.drop_empty_chunks and not text:
                continue
            order_field, order_value = self._resolve_order_value(hit=hit)
            sort_group, sort_value = self._build_sort_key(order_field=order_field, order_value=order_value)
            resolved_chunks.append(
                _ResolvedChunk(
                    hit=hit,
                    normalized_text=text,
                    order_field=order_field,
                    order_value=order_value,
                    sort_group=sort_group,
                    sort_value=sort_value,
                    original_index=index,
                )
            )
        return sorted(
            resolved_chunks,
            key=lambda item: (item.sort_group, item.sort_value, item.original_index),
        )

    def _resolve_order_value(self, *, hit: SearchHit) -> tuple[str | None, ScalarValue | None]:
        for field_path in self._config.window.order_fields:
            value = self._resolve_value(target=hit, field_path=field_path)
            if isinstance(value, (str, int, float, bool)):
                return field_path, value
        return None, None

    def _build_sort_key(
        self,
        *,
        order_field: str | None,
        order_value: ScalarValue | None,
    ) -> tuple[int, float | str | int]:
        if isinstance(order_value, bool):
            return 1, int(order_value)
        if isinstance(order_value, (int, float)):
            return 0, float(order_value)
        if isinstance(order_value, str):
            numeric_value = self._try_parse_number(order_value)
            if numeric_value is not None:
                return 0, numeric_value
            return 1, order_value
        if order_field == "id":
            return 0, 0
        return 2, 0

    def _should_merge_chunks(
        self,
        *,
        previous: _ResolvedChunk,
        current: _ResolvedChunk,
        current_window: list[_ResolvedChunk],
    ) -> bool:
        if not self._config.window.merge_adjacent_chunks:
            return False
        if len(current_window) >= self._config.window.max_chunks_per_window:
            return False
        if previous.order_field is None or current.order_field is None:
            return False
        if previous.order_field != current.order_field:
            return False
        if not self._supports_adjacency(previous.order_field):
            return False
        if not isinstance(previous.order_value, (int, float)) or isinstance(previous.order_value, bool):
            return False
        if not isinstance(current.order_value, (int, float)) or isinstance(current.order_value, bool):
            return False
        gap = float(current.order_value) - float(previous.order_value)
        return 0 <= gap <= self._config.window.adjacent_order_gap

    def _supports_adjacency(self, order_field: str) -> bool:
        lowered = order_field.lower()
        return any(token in lowered for token in ("chunk_index", "position", "order", "seq", "sequence"))

    def _build_document_header(
        self,
        *,
        document: CollapsedSearchHit,
        document_index: int,
        title: str | None,
        source: str | None,
    ) -> str | None:
        if not self._config.header.enabled:
            return None

        parts = [f"Document {document_index}"]
        if self._config.header.include_document_key:
            parts.append(f"key={document.document_key}")
        if title:
            parts.append(f"title={title}")
        if source:
            parts.append(f"source={source}")
        if self._config.header.include_retrieval_mode and document.retrieval_mode is not None:
            parts.append(f"mode={document.retrieval_mode.value}")
        return f"[{' | '.join(parts)}]"

    def _build_window_header(
        self,
        *,
        window_index: int,
        chunk_ids: list[int],
        section: str | None,
        page: ScalarValue | None,
    ) -> str | None:
        if not self._config.window.include_headers:
            return None

        parts = [f"Window {window_index}", f"chunks={','.join(str(chunk_id) for chunk_id in chunk_ids)}"]
        if section:
            parts.append(f"section={section}")
        if page is not None:
            parts.append(f"page={page}")
        return f"[{' | '.join(parts)}]"

    def _resolve_document_value(
        self,
        *,
        document: CollapsedSearchHit,
        field_paths: list[str],
    ) -> Any:
        for field_path in field_paths:
            value = self._resolve_value(target=document, field_path=field_path)
            if value is not None:
                return value
        for field_path in field_paths:
            for chunk in document.chunks:
                value = self._resolve_value(target=chunk, field_path=field_path)
                if value is not None:
                    return value
        return None

    def _resolve_window_value(
        self,
        *,
        chunks: list[_ResolvedChunk],
        field_paths: list[str],
    ) -> Any:
        values: list[Any] = []
        for field_path in field_paths:
            values.clear()
            for chunk in chunks:
                value = self._resolve_value(target=chunk.hit, field_path=field_path)
                if value is not None:
                    values.append(value)
            if not values:
                continue
            unique_values = {value for value in values}
            if len(unique_values) == 1:
                return values[0]
            return self._format_range(values=values)
        return None

    def _resolve_value(self, *, target: BaseModel | dict[str, Any], field_path: str) -> Any:
        current: Any = target
        for part in field_path.split("."):
            if isinstance(current, BaseModel):
                current = getattr(current, part, None)
            elif isinstance(current, dict):
                current = current.get(part)
            else:
                current = getattr(current, part, None)
            if current is None:
                return None
        return current

    def _resolve_document_budget(
        self,
        *,
        remaining_total_chars: int | None,
        separator: str,
    ) -> int | None:
        budget = self._config.budget.max_document_chars
        if remaining_total_chars is None:
            return budget
        available = remaining_total_chars - len(separator)
        if budget is None:
            return available
        return min(budget, available)

    def _normalize_text(self, text: str | None) -> str:
        if not text:
            return ""
        normalized = text.strip()
        if not self._config.normalize_whitespace:
            return normalized
        return re.sub(r"\n{3,}", "\n\n", normalized)

    def _truncate_text(self, text: str, limit: int) -> str:
        if limit <= 0:
            return ""
        if len(text) <= limit:
            return text
        if len(self._config.ellipsis) >= limit:
            return text[:limit]
        truncated = text[: limit - len(self._config.ellipsis)].rstrip()
        return f"{truncated}{self._config.ellipsis}"

    def _coerce_text(self, value: Any) -> str | None:
        if value is None:
            return None
        text = self._normalize_text(str(value))
        return text or None

    def _format_range(self, *, values: list[Any]) -> Any:
        numeric_values = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
        if len(numeric_values) == len(values):
            start = min(numeric_values)
            end = max(numeric_values)
            if start == end:
                return start
            return f"{start}-{end}"
        return str(values[0])

    def _try_parse_number(self, value: str) -> float | None:
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
