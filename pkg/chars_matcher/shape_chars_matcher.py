"""ASR 姓名场景下的字形候选排序。

本模块作为拼音匹配的辅助排序器，在已知拼音候选集内部，
利用 shape_neighbors 和 component_tokens 做轻量字形重排。

设计目标
========
1. 利用少量人工 shape family（shape_neighbors）做高置信排序信号
2. 利用 component_tokens 做弱相似度排序信号
3. 不独立产出候选，只对外部传入的候选集排序

默认数据来源
==========
- `chars/shape/name_shape_chars.json`: 姓名字库专用的精简字形数据
  - component_tokens
  - shape_neighbors（已包含人工 curated family）
"""

from __future__ import annotations

import heapq
import json
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from pkg.chars_matcher.types import CharShapeFeature, ShapeFeatureMapping
from pkg.chars_matcher.validation import normalize_single_han_char
from pkg.concurrency import anyio_run_in_thread

type ShapeCandidateRank = tuple[int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class _RankedShapeCandidate:
    char: str
    rank: ShapeCandidateRank
    input_order: int


class ShapeCharsMatcher:
    """按字形特征对外部候选集做轻量重排。"""

    _DEFAULT_SHAPE_FEATURES_PATH = Path(__file__).resolve().parent / "chars" / "shape" / "name_shape_chars.json"
    _SHAPE_NEIGHBOR_MISS_RANK = 1_000_000

    def __init__(
        self,
        *,
        shape_features_path: Path | None = None,
    ) -> None:
        self._shape_features_path = shape_features_path or self._DEFAULT_SHAPE_FEATURES_PATH
        self._preloaded = False
        self._preload_lock = threading.Lock()

    async def preload(self) -> None:
        """异步预加载姓名字形数据及倒排索引。"""
        if self._preloaded:
            return
        await anyio_run_in_thread(self._ensure_loaded)

    def _ensure_loaded(self) -> None:
        """确保数据已加载（线程安全）。

        无论是否调用过 preload()，首次访问时都会通过锁保护初始化，
        避免 Python 3.12+ cached_property 无锁导致的并发重复加载。
        """
        if self._preloaded:
            return
        with self._preload_lock:
            if self._preloaded:
                return
            _ = self._shape_features
            self._preloaded = True

    @cached_property
    def _shape_features(self) -> ShapeFeatureMapping:
        return self._load_shape_features()

    def _load_shape_features(self) -> ShapeFeatureMapping:
        data = json.loads(self._shape_features_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{self._shape_features_path.name} 内容格式无效，期望 dict[str, feature]")

        normalized: ShapeFeatureMapping = {}
        for char, raw_feature in data.items():
            if not isinstance(char, str) or not isinstance(raw_feature, dict):
                continue

            feature: CharShapeFeature = {}

            component_tokens = raw_feature.get("component_tokens")
            if isinstance(component_tokens, list):
                feature["component_tokens"] = [token for token in component_tokens if isinstance(token, str) and token]

            shape_neighbors = raw_feature.get("shape_neighbors")
            if isinstance(shape_neighbors, list):
                feature["shape_neighbors"] = [
                    neighbor for neighbor in shape_neighbors if isinstance(neighbor, str) and neighbor
                ]

            if feature:
                normalized[char] = feature

        return normalized

    def _shape_feature(self, *, char: str) -> CharShapeFeature | None:
        self._ensure_loaded()
        return self._shape_features.get(char)

    def _component_tokens_without_self(self, *, char: str) -> tuple[str, ...]:
        feature = self._shape_feature(char=char)
        if feature is None:
            return ()
        tokens = feature.get("component_tokens", [])
        # dict.fromkeys 保序去重，再排除自身
        return tuple(t for t in dict.fromkeys(tokens) if t != char)

    @staticmethod
    def _validate_single_char_query(query_text: str) -> str:
        return normalize_single_han_char(query_text, matcher_name="shape matcher")

    @staticmethod
    def _deduplicate_candidates(
        *,
        candidates: Iterable[str],
    ) -> list[str]:
        return list(dict.fromkeys(c for c in candidates if c))

    def _neighbor_index(self, *, source: str, target: str) -> int:
        """返回 target 在 source 的 shape_neighbors 中的位置，未找到返回 miss 值。"""
        feature = self._shape_feature(char=source)
        if feature is None:
            return self._SHAPE_NEIGHBOR_MISS_RANK
        try:
            return feature.get("shape_neighbors", []).index(target)
        except ValueError:
            return self._SHAPE_NEIGHBOR_MISS_RANK

    def _contains_query_score(self, *, query_text: str, char: str) -> int:
        candidate_tokens = self._component_tokens_without_self(char=char)
        if not candidate_tokens or query_text not in candidate_tokens:
            return 2
        return 0 if candidate_tokens[0] == query_text else 1

    def _component_score(self, *, query_text: str, char: str) -> int:
        query_tokens = self._component_tokens_without_self(char=query_text)
        candidate_tokens = self._component_tokens_without_self(char=char)
        if not query_tokens or not candidate_tokens:
            return 3

        query_pos = {t: i for i, t in enumerate(query_tokens)}
        cand_pos = {t: i for i, t in enumerate(candidate_tokens)}
        shared = query_pos.keys() & cand_pos.keys()
        if not shared:
            return 3
        return 1 if any(query_pos[t] == 0 or cand_pos[t] == 0 for t in shared) else 2

    def _candidate_rank(self, *, query_text: str, char: str) -> ShapeCandidateRank:
        return (
            int(char != query_text),
            self._neighbor_index(source=query_text, target=char),
            self._neighbor_index(source=char, target=query_text),
            self._contains_query_score(query_text=query_text, char=char),
            self._component_score(query_text=query_text, char=char),
        )

    def _sorted_candidates_by_rank(
        self,
        *,
        query_text: str,
        candidate_pool: Iterable[str],
        n: int | None = None,
    ) -> list[str]:
        ranked_candidates = [
            _RankedShapeCandidate(
                char=char,
                rank=self._candidate_rank(query_text=query_text, char=char),
                input_order=index,
            )
            for index, char in enumerate(candidate_pool)
        ]

        def rank_key(candidate: _RankedShapeCandidate) -> tuple[ShapeCandidateRank, int]:
            return (candidate.rank, candidate.input_order)

        if n is not None and n < len(ranked_candidates):
            top = heapq.nsmallest(n, ranked_candidates, key=rank_key)
        else:
            top = sorted(ranked_candidates, key=rank_key)
        return [candidate.char for candidate in top]

    def top_n_by_shape(
        self,
        query_text: str,
        *,
        candidates: Iterable[str],
        n: int,
    ) -> list[str]:
        """返回候选集中字形最相似的前 n 个字（不含 query_text 自身）。"""
        normalized_query = self._validate_single_char_query(query_text)
        if not normalized_query:
            return []
        candidate_pool = self._deduplicate_candidates(candidates=candidates)
        # +1 预留原字可能占位，取出后再过滤
        top = self._sorted_candidates_by_rank(
            query_text=normalized_query,
            candidate_pool=candidate_pool,
            n=n + 1,
        )
        return [c for c in top if c != normalized_query][:n]

    def sort_candidates_by_shape(
        self,
        query_text: str,
        *,
        candidates: Iterable[str],
    ) -> list[str]:
        """对外部传入的候选集按字形相似度排序。"""
        normalized_query = self._validate_single_char_query(query_text)
        if not normalized_query:
            return []
        candidate_pool = self._deduplicate_candidates(candidates=candidates)
        return self._sorted_candidates_by_rank(
            query_text=normalized_query,
            candidate_pool=candidate_pool,
        )


__all__ = ["ShapeCharsMatcher"]
