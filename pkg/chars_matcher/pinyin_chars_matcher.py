"""根据拼音查找中文候选字。

拼音匹配策略概述
================
本模块实现输入法风格的拼音模糊匹配，支持普通汉字和姓氏候选。

匹配类型（按优先级）
-------------
1. **exact**：精确匹配，用户输入与拼音库中的 key 完全一致
2. **fuzzy_exact**：模糊音精确匹配，通过声母/韵母模糊规则转换后匹配
3. **prefix**：前缀匹配，用户输入是拼音库 key 的前缀

模糊音规则
---------
- 声母模糊：z/zh, c/ch, s/sh, l/n, f/h, r/l
- 韵母模糊：an/ang, en/eng, in/ing, ian/iang, uan/uang

核心排序维度（按优先级）
----------------------
1. **匹配类型**：exact > fuzzy_exact > prefix
2. **模糊音编辑数**：应用了几条模糊规则
3. **前缀多余字符数**：前缀匹配时，拼音 key 比用户输入多出的字符数
4. **精确查询惩罚**：是否与用户提供的 query_text 完全一致
5. **规范拼音惩罚**：候选字的规范拼音是否与匹配 key 一致
6. **桶内顺序**：沿用映射表中的预排序结果，静态优先级由离线构建确定

姓氏匹配特殊处理
-------------
姓氏匹配允许在精确匹配后仍然尝试前缀匹配，因为姓氏输入场景中
用户可能只输入部分拼音。

数据来源
-------
- `chars/pinyin/pinyin_chars.json`: 拼音 → 汉字列表的映射
- `chars/pinyin/surname_pinyin_chars.json`: 姓氏拼音映射（包含多音字姓氏读音）
"""

from __future__ import annotations

import json
import re
import threading
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Generic, NamedTuple, TypeVar

from pypinyin import lazy_pinyin

from pkg.chars_matcher.types import CharMatchType, MatchType, PinyinCharMapping
from pkg.concurrency import anyio_run_in_thread

_CacheKeyT = TypeVar("_CacheKeyT")
_CacheValueT = TypeVar("_CacheValueT")


class _PinyinCandidateRank(NamedTuple):
    """拼音候选排序 key，按字段顺序做字典序比较。"""

    # exact < fuzzy_exact < prefix
    match_type_priority: int
    # 应用了几条模糊音规则，越少越优先
    fuzzy_edits: int
    # 前缀匹配时 key 相比输入多出的字符数
    prefix_extra_chars: int
    # 与原始 query_text 完全一致时为 0，否则为 1
    exact_query_penalty: int
    # 候选字首选规范拼音等于 matched_key.key 时为 0，否则为 1
    canonical_pinyin_penalty: int
    # 候选字在当前拼音桶中的原始顺序
    bucket_index: int


@dataclass(slots=True)
class _BestEffortLruCache(Generic[_CacheKeyT, _CacheValueT]):
    """实例内 best-effort LRU。

    这些运行时缓存只用于减少重复计算，不承载业务正确性语义，
    因此不追求强线程安全或强一致性。并发竞争时允许退化为 miss
    或放弃写入，避免为缓存命中率引入额外锁。
    """

    _max_size: int
    _items: OrderedDict[_CacheKeyT, _CacheValueT] = field(default_factory=OrderedDict)

    def get(self, key: _CacheKeyT) -> _CacheValueT | None:
        cached_value = self._items.get(key)
        if cached_value is None:
            return None
        try:
            self._items.move_to_end(key)
        except KeyError:
            return None
        return cached_value

    def set(self, key: _CacheKeyT, value: _CacheValueT) -> None:
        try:
            self._items[key] = value
            self._items.move_to_end(key)
            if len(self._items) > self._max_size:
                self._items.popitem(last=False)
        except (KeyError, RuntimeError):
            return


@dataclass(frozen=True, slots=True)
class _FuzzyVariant:
    token: str
    fuzzy_edits: int


@dataclass(frozen=True, slots=True)
class _MatchedPinyinKey:
    key: str
    match_type: MatchType
    fuzzy_edits: int
    prefix_extra_chars: int = 0


@dataclass(frozen=True, slots=True)
class _RankedPinyinCandidate:
    char: str
    rank: _PinyinCandidateRank
    first_seen: int


@dataclass(slots=True)
class _PinyinPrefixTrieNode:
    children: dict[str, _PinyinPrefixTrieNode] = field(default_factory=dict)
    prefixed_keys: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _CharTypeResources:
    mapping: PinyinCharMapping
    prefix_trie: _PinyinPrefixTrieNode
    text_to_pinyin_keys: dict[str, tuple[str, ...]]


class PinyinCharsMatcher:
    """按拼音匹配普通汉字或姓氏候选字,并做拼音层面的稳定排序。

    该类实现了输入法风格的模糊拼音匹配算法,支持声母/韵母模糊、
    前缀匹配等多种匹配模式。

    线程模型说明：
        本类内部的 LRU 缓存仅用于性能优化，不保证强线程安全或
        强一致性。并发访问同一实例时，缓存允许退化为 miss、重复
        计算或近似 LRU 顺序，但不应影响匹配结果正确性。

    Attributes:
        _FUZZY_INITIALS: 声母模糊映射表(如 z ↔ zh)
        _FUZZY_FINALS: 韵母模糊映射表(如 an ↔ ang)
        _MATCH_TYPE_PRIORITIES: 匹配类型优先级(exact < fuzzy_exact < prefix)

    Example:
        >>> matcher = PinyinCharsMatcher()
        >>> matcher.match_chars_by_pinyin("li")
        ['李', '理', '里', '力', ...]
        >>> matcher.match_chars_by_pinyin("zhang", char_type="surname")
        ['张', '章', '彰', ...]
    """

    _PINYIN_SPLIT_PATTERN = re.compile(r"[\s,，;；/|]+")
    _NON_ALPHA_PATTERN = re.compile(r"[^a-z]")
    _DEFAULT_CHARS_DIR = Path(__file__).resolve().parent / "chars"
    _DEFAULT_PINYIN_DIR_NAME = "pinyin"
    _UMLAUT_VOWEL_TRANSLATION = str.maketrans(
        {
            "ü": "v",
            "ǖ": "v",
            "ǘ": "v",
            "ǚ": "v",
            "ǜ": "v",
        }
    )

    # 输入法风格的模糊音映射表
    # 声母模糊：z/zh, c/ch, s/sh, l/n, f/h, r/l
    # 韵母模糊：an/ang, en/eng, in/ing, ian/iang, uan/uang
    _FUZZY_INITIALS: dict[str, list[str]] = {
        "z": ["zh"],
        "zh": ["z"],
        "c": ["ch"],
        "ch": ["c"],
        "s": ["sh"],
        "sh": ["s"],
        "l": ["n", "r"],
        "n": ["l"],
        "r": ["l"],
        "f": ["h"],
        "h": ["f"],
    }
    _FUZZY_FINALS: dict[str, list[str]] = {
        "an": ["ang"],
        "ang": ["an"],
        "en": ["eng"],
        "eng": ["en"],
        "in": ["ing"],
        "ing": ["in"],
        "ian": ["iang"],
        "iang": ["ian"],
        "uan": ["uang"],
        "uang": ["uan"],
    }
    _MATCH_TYPE_PRIORITIES: dict[MatchType, int] = {
        "exact": 0,
        "fuzzy_exact": 1,
        "prefix": 2,
    }
    _CANONICAL_PINYIN_CACHE_MAX_SIZE = 2048
    _MATCHED_PINYIN_KEYS_CACHE_MAX_SIZE = 2048
    _RESOLVED_TEXT_PINYIN_KEYS_CACHE_MAX_SIZE = 2048
    _RESULT_CACHE_MAX_SIZE = 256
    _SINGLE_CHAR_SURNAME_PREFERRED_PINYIN_KEYS: dict[str, str] = {
        # 这些姓氏在词库中保留了多个读音，展示/排序不能继续依赖 JSON key 顺序。
        "乐": "yue",
        "单": "shan",
        "盖": "ge",
        "车": "ju",
        "隗": "wei",
    }

    def __init__(self, *, chars_dir: Path | None = None) -> None:
        self._chars_dir = chars_dir or self._DEFAULT_CHARS_DIR
        # 仅缓存 pypinyin 兜底推断结果；当前仍随 matcher 实例生命周期管理。
        self._canonical_pinyin_cache = _BestEffortLruCache[str, str](
            _max_size=self._CANONICAL_PINYIN_CACHE_MAX_SIZE,
        )
        # 依赖当前实例词库和 prefix trie，不能脱离 matcher 上下文理解。
        self._matched_pinyin_keys_cache = _BestEffortLruCache[
            tuple[str, CharMatchType, bool],
            tuple[_MatchedPinyinKey, ...],
        ](_max_size=self._MATCHED_PINYIN_KEYS_CACHE_MAX_SIZE)
        # 依赖当前实例词库索引、姓氏 fallback 和首选读音规则。
        self._resolved_text_pinyin_keys_cache = _BestEffortLruCache[
            tuple[str, CharMatchType],
            tuple[str, ...],
        ](_max_size=self._RESOLVED_TEXT_PINYIN_KEYS_CACHE_MAX_SIZE)
        # 最终候选结果缓存，间接依赖上面所有词库相关缓存和排序逻辑。
        self._match_result_cache = _BestEffortLruCache[
            tuple[tuple[str, ...], CharMatchType, str | None],
            tuple[str, ...],
        ](_max_size=self._RESULT_CACHE_MAX_SIZE)
        # 异步加载支持
        self._preloaded = False
        self._preload_lock = threading.Lock()

    async def preload(self) -> None:
        """异步预加载拼音数据与索引，避免首次请求时的阻塞。

        建议在应用启动时调用，例如：
            async def lifespan(app: FastAPI):
                await matcher.preload()
                yield

        多次调用是安全的，后续调用会立即返回。
        """
        if self._preloaded:
            return
        # 在后台线程中执行同步加载，避免阻塞事件循环
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
            # 触发 cached_property 加载，包括前缀查询使用的 Trie
            _ = self._char_mapping
            _ = self._surname_mapping
            _ = self._char_prefix_trie
            _ = self._surname_prefix_trie
            _ = self._char_text_to_pinyin_keys
            _ = self._surname_text_to_pinyin_keys
            self._preloaded = True

    @cached_property
    def _char_mapping(self) -> PinyinCharMapping:
        return self._load_char_mapping("pinyin_chars.json")

    @cached_property
    def _surname_mapping(self) -> PinyinCharMapping:
        return self._load_char_mapping("surname_pinyin_chars.json")

    @cached_property
    def _char_prefix_trie(self) -> _PinyinPrefixTrieNode:
        return self._build_prefix_trie(self._char_mapping)

    @cached_property
    def _surname_prefix_trie(self) -> _PinyinPrefixTrieNode:
        return self._build_prefix_trie(self._surname_mapping)

    @cached_property
    def _char_text_to_pinyin_keys(self) -> dict[str, tuple[str, ...]]:
        return self._build_text_to_pinyin_keys_index(self._char_mapping)

    @cached_property
    def _surname_text_to_pinyin_keys(self) -> dict[str, tuple[str, ...]]:
        return self._build_text_to_pinyin_keys_index(self._surname_mapping)

    def _load_char_mapping(self, file_name: str) -> PinyinCharMapping:
        data = json.loads(
            (self._chars_dir / self._DEFAULT_PINYIN_DIR_NAME / file_name).read_text(
                encoding="utf-8"
            )
        )
        if not isinstance(data, dict):
            raise ValueError(f"{file_name} 内容格式无效，期望 dict[str, list[str]]")

        normalized: PinyinCharMapping = {}
        for key, value in data.items():
            if not isinstance(key, str) or not isinstance(value, list):
                raise ValueError(f"{file_name} 内容格式无效，期望 dict[str, list[str]]")
            normalized[key] = [item for item in value if isinstance(item, str)]
        return normalized

    @staticmethod
    def _build_prefix_trie(mapping: PinyinCharMapping) -> _PinyinPrefixTrieNode:
        root = _PinyinPrefixTrieNode()
        for key in mapping:
            node = root
            node.prefixed_keys.append(key)
            for char in key:
                node = node.children.setdefault(char, _PinyinPrefixTrieNode())
                node.prefixed_keys.append(key)
        return root

    @staticmethod
    def _build_text_to_pinyin_keys_index(
        mapping: PinyinCharMapping,
    ) -> dict[str, tuple[str, ...]]:
        reverse_index: dict[str, list[str]] = {}
        for key, texts in mapping.items():
            for text in texts:
                reverse_index.setdefault(text, []).append(key)
        return {
            text: tuple(pinyin_keys)
            for text, pinyin_keys in reverse_index.items()
        }

    @classmethod
    def normalize_pinyin_token(cls, token: str) -> str:
        """规范化单个拼音片段，生成后续匹配使用的查询 key。

        规范化规则：
        - 大写转小写
        - `u:` / `ü` / 带音调的 `ü` 统一为内部使用的 `v`
        - 去掉其余音调符号与非字母字符
        """
        normalized_token = token.strip().lower().replace("u:", "v").replace("u：", "v")
        normalized_token = normalized_token.translate(cls._UMLAUT_VOWEL_TRANSLATION)
        normalized_token = unicodedata.normalize("NFKD", normalized_token)
        normalized_token = "".join(
            char for char in normalized_token if not unicodedata.combining(char)
        )
        return cls._NON_ALPHA_PATTERN.sub("", normalized_token)

    @classmethod
    def split_pinyin_query(cls, pinyin: str) -> list[str]:
        """将用户输入拆分为规范化拼音 token 列表，供后续逐个匹配。"""
        if not pinyin.strip():
            return []

        return [
            normalized
            for normalized in (
                cls.normalize_pinyin_token(part)
                for part in cls._PINYIN_SPLIT_PATTERN.split(pinyin.strip())
            )
            if normalized
        ]

    @classmethod
    def _generate_fuzzy_variants(cls, token: str) -> list[_FuzzyVariant]:
        """生成拼音的模糊音变体，并记录变体与原始 token 的编辑距离。

        生成策略：
        1. 解析声母和韵母（如 "zhang" → "zh" + "ang"）
        2. 对声母应用模糊规则（如 zh → z）
        3. 对韵母应用模糊规则（如 ang → an）
        4. 组合所有变体，记录编辑次数

        Args:
            token: 规范化后的拼音 token（小写、无空格）

        Returns:
            变体列表，第一个始终是原始 token（fuzzy_edits=0）
        """
        variants: dict[str, int] = {token: 0}

        initial = ""
        final = token
        if token.startswith(("zh", "ch", "sh")):
            initial = token[:2]
            final = token[2:]
        elif token and token[0] in "bpmfdtnlgkhjqxrzcsyw":
            initial = token[0]
            final = token[1:]

        initial_variants: list[tuple[str, int]] = [(initial, 0)]
        if initial in cls._FUZZY_INITIALS:
            initial_variants.extend(
                (candidate, 1) for candidate in cls._FUZZY_INITIALS[initial]
            )

        final_variants: list[tuple[str, int]] = [(final, 0)]
        for fuzzy_final, replacements in cls._FUZZY_FINALS.items():
            if final.endswith(fuzzy_final):
                prefix = final[: -len(fuzzy_final)]
                final_variants.extend(
                    (prefix + replacement, 1) for replacement in replacements
                )

        for init, init_edits in initial_variants:
            for fin, final_edits in final_variants:
                variant = init + fin
                edits = init_edits + final_edits
                if variant and edits < variants.get(variant, edits + 1):
                    variants[variant] = edits

        return [
            _FuzzyVariant(token=variant_token, fuzzy_edits=fuzzy_edits)
            for variant_token, fuzzy_edits in variants.items()
        ]

    def _lazy_text_to_pinyin_key(self, text: str) -> str:
        """使用通用拼音库推断默认拼音 key，作为缺省兜底。"""
        cached_key = self._canonical_pinyin_cache.get(text)
        if cached_key is not None:
            return cached_key

        normalized_key = "".join(
            self.normalize_pinyin_token(token)
            for token in lazy_pinyin(text)
        )
        self._canonical_pinyin_cache.set(
            text,
            normalized_key,
        )
        return normalized_key

    def _get_resources(self, char_type: CharMatchType) -> _CharTypeResources:
        self._ensure_loaded()
        if char_type == "char":
            return _CharTypeResources(
                self._char_mapping,
                self._char_prefix_trie,
                self._char_text_to_pinyin_keys,
            )
        if char_type == "surname":
            return _CharTypeResources(
                self._surname_mapping,
                self._surname_prefix_trie,
                self._surname_text_to_pinyin_keys,
            )
        raise ValueError(f"不支持的 char_type: {char_type}")

    def _context_preferred_pinyin_key(
        self,
        text: str,
        keys: tuple[str, ...],
        *,
        char_type: CharMatchType,
    ) -> str:
        if char_type == "surname" and len(text) == 1:
            override = self._SINGLE_CHAR_SURNAME_PREFERRED_PINYIN_KEYS.get(text)
            if override and override in keys:
                return override
        preferred = self._lazy_text_to_pinyin_key(text)
        return preferred if preferred in keys else ""

    def _order_pinyin_keys_by_preferred(
        self,
        text: str,
        keys: tuple[str, ...],
        *,
        char_type: CharMatchType,
    ) -> tuple[str, ...]:
        """将 preferred pinyin key 排到首位，其余保持原序。"""
        preferred = self._context_preferred_pinyin_key(
            text,
            keys,
            char_type=char_type,
        )
        if not preferred:
            return keys
        return (preferred, *(k for k in keys if k != preferred))

    def resolve_text_to_pinyin_keys(
        self,
        text: str,
        *,
        char_type: CharMatchType = "char",
    ) -> tuple[str, ...]:
        """根据 matcher 自身词库解析文本的候选拼音 key。"""
        normalized_text = text.strip()
        if not normalized_text:
            return ()

        cache_key = (normalized_text, char_type)
        cached_keys = self._resolved_text_pinyin_keys_cache.get(cache_key)
        if cached_keys is not None:
            return cached_keys

        # 1. 从当前 char_type 索引查找
        indexed_keys = self._get_resources(char_type).text_to_pinyin_keys.get(
            normalized_text, (),
        )
        if indexed_keys:
            if char_type == "surname" and len(normalized_text) == 2:
                resolved_keys = indexed_keys[:1]  # 复姓只取第一个
            else:
                resolved_keys = self._order_pinyin_keys_by_preferred(
                    normalized_text,
                    indexed_keys,
                    char_type=char_type,
                )
        # 2. 姓氏单字 fallback 到普通字库
        elif char_type == "surname" and len(normalized_text) == 1:
            char_keys = self._char_text_to_pinyin_keys.get(normalized_text, ())
            resolved_keys = (
                self._order_pinyin_keys_by_preferred(
                    normalized_text,
                    char_keys,
                    char_type=char_type,
                )
                if char_keys
                else ()
            )
        # 3. 兜底：pypinyin 推断
        else:
            resolved_keys = ()

        if not resolved_keys:
            fallback = self._lazy_text_to_pinyin_key(normalized_text)
            resolved_keys = (fallback,) if fallback else ()

        self._resolved_text_pinyin_keys_cache.set(
            cache_key,
            resolved_keys,
        )
        return resolved_keys

    def preferred_text_pinyin_key(
        self,
        text: str,
        *,
        char_type: CharMatchType = "char",
    ) -> str:
        """返回文本在当前匹配上下文中的首选展示拼音 key。"""
        resolved_keys = self.resolve_text_to_pinyin_keys(text, char_type=char_type)
        if resolved_keys:
            return resolved_keys[0]
        return ""

    def _candidate_rank(
        self,
        *,
        char: str,
        char_type: CharMatchType,
        matched_key: _MatchedPinyinKey,
        query_text: str | None,
        bucket_index: int,
    ) -> _PinyinCandidateRank:
        """计算候选字的综合排序 rank（tuple 比较，越小越靠前）。

        排序维度（按优先级）：
        1. 匹配类型优先级：exact(0) < fuzzy_exact(1) < prefix(2)
        2. 模糊编辑数：应用了几条模糊音规则
        3. 前缀多余字符：前缀匹配时 key 比输入多出的字符数
        4. 精确查询惩罚：0=与 query_text 完全一致，1=不一致或未提供
        5. 规范拼音惩罚：0=候选字规范拼音等于匹配 key，1=不等
        6. 桶内顺序：映射表中越靠前越优先

        Args:
            char: 候选汉字
            matched_key: 匹配信息（key、匹配类型、模糊编辑数等）
            query_text: 用户提供的原始查询文本（用于精确匹配加权）
            bucket_index: 候选字在当前拼音桶中的位置（0-based）
        """
        canonical_penalty = int(
            self.preferred_text_pinyin_key(char, char_type=char_type) != matched_key.key
        )
        exact_query_penalty = int(query_text is None or char != query_text)
        return _PinyinCandidateRank(
            match_type_priority=self._MATCH_TYPE_PRIORITIES[matched_key.match_type],
            fuzzy_edits=matched_key.fuzzy_edits,
            prefix_extra_chars=matched_key.prefix_extra_chars,
            exact_query_penalty=exact_query_penalty,
            canonical_pinyin_penalty=canonical_penalty,
            bucket_index=bucket_index,
        )

    def _match_pinyin_keys(
        self,
        token: str,
        mapping: PinyinCharMapping,
        prefix_trie: _PinyinPrefixTrieNode,
        *,
        allow_prefix_after_exact: bool = False,
    ) -> list[_MatchedPinyinKey]:
        """输入法风格的模糊拼音匹配。

        匹配顺序（依次尝试）：
        1. 先尝试精确匹配（token 在 mapping 中）
        2. 再尝试模糊音精确匹配（模糊变体在 mapping 中）
        3. 最后尝试前缀匹配（仅当前面无匹配，或 allow_prefix_after_exact=True）

        Args:
            token: 规范化后的拼音 token
            mapping: 拼音到汉字列表的映射
            prefix_trie: 与 mapping 对应的前缀 Trie，用于加速 prefix 查询
            allow_prefix_after_exact: 是否在精确匹配后仍尝试前缀匹配
                （姓氏匹配场景需要设为 True）

        Returns:
            匹配到的 key 列表（已去重）
        """
        matched_keys: list[_MatchedPinyinKey] = []
        seen: set[str] = set()

        def add(match: _MatchedPinyinKey) -> None:
            if match.key not in seen:
                matched_keys.append(match)
                seen.add(match.key)

        if token in mapping:
            add(_MatchedPinyinKey(key=token, match_type="exact", fuzzy_edits=0))

        fuzzy_variants = self._generate_fuzzy_variants(token)
        for variant in fuzzy_variants[1:]:
            if variant.token in mapping:
                add(
                    _MatchedPinyinKey(
                        key=variant.token,
                        match_type="fuzzy_exact",
                        fuzzy_edits=variant.fuzzy_edits,
                    )
                )

        if allow_prefix_after_exact or not matched_keys:
            for variant in fuzzy_variants:
                for key in self._prefix_matches(prefix_trie, variant.token):
                    add(
                        _MatchedPinyinKey(
                            key=key,
                            match_type="prefix",
                            fuzzy_edits=variant.fuzzy_edits,
                            prefix_extra_chars=len(key) - len(variant.token),
                        )
                    )

        return matched_keys

    @staticmethod
    def _prefix_matches(
        prefix_trie: _PinyinPrefixTrieNode,
        prefix: str,
    ) -> list[str]:
        node = prefix_trie
        for char in prefix:
            next_node = node.children.get(char)
            if next_node is None:
                return []
            node = next_node
        return node.prefixed_keys

    def _get_matched_pinyin_keys(
        self,
        token: str,
        *,
        char_type: CharMatchType,
        allow_prefix_after_exact: bool,
    ) -> tuple[_MatchedPinyinKey, ...]:
        cache_key = (token, char_type, allow_prefix_after_exact)
        cached_matches = self._matched_pinyin_keys_cache.get(cache_key)
        if cached_matches is not None:
            return cached_matches

        resources = self._get_resources(char_type)
        matches = tuple(
            self._match_pinyin_keys(
                token,
                resources.mapping,
                resources.prefix_trie,
                allow_prefix_after_exact=allow_prefix_after_exact,
            )
        )
        self._matched_pinyin_keys_cache.set(
            cache_key,
            matches,
        )
        return matches

    def _get_cached_match_result(
        self,
        *,
        tokens: tuple[str, ...],
        char_type: CharMatchType,
        query_text: str | None,
    ) -> list[str] | None:
        cache_key = (tokens, char_type, query_text)
        cached_result = self._match_result_cache.get(cache_key)
        if cached_result is None:
            return None

        return list(cached_result)

    def _cache_match_result(
        self,
        *,
        tokens: tuple[str, ...],
        char_type: CharMatchType,
        query_text: str | None,
        result: list[str],
    ) -> None:
        cache_key = (tokens, char_type, query_text)
        self._match_result_cache.set(
            cache_key,
            tuple(result),
        )

    def _match_chars_by_tokens(
        self,
        tokens: tuple[str, ...],
        *,
        char_type: CharMatchType,
        query_text: str | None,
    ) -> list[str]:
        mapping = self._get_resources(char_type).mapping
        if not tokens:
            return []

        cached_result = self._get_cached_match_result(
            tokens=tokens,
            char_type=char_type,
            query_text=query_text,
        )
        if cached_result is not None:
            return cached_result

        ranked_candidates: dict[str, _RankedPinyinCandidate] = {}
        first_seen = 0
        allow_prefix_after_exact = char_type == "surname"
        for token in tokens:
            for matched_key in self._get_matched_pinyin_keys(
                token,
                char_type=char_type,
                allow_prefix_after_exact=allow_prefix_after_exact,
            ):
                for bucket_index, char in enumerate(mapping.get(matched_key.key, [])):
                    first_seen += 1
                    rank = self._candidate_rank(
                        char=char,
                        char_type=char_type,
                        matched_key=matched_key,
                        query_text=query_text,
                        bucket_index=bucket_index,
                    )
                    existing = ranked_candidates.get(char)
                    if existing is None or rank < existing.rank:
                        ranked_candidates[char] = _RankedPinyinCandidate(
                            char=char,
                            rank=rank,
                            first_seen=first_seen,
                        )

        result = [
            candidate.char
            for candidate in sorted(
                ranked_candidates.values(),
                key=lambda candidate: (candidate.rank, candidate.first_seen),
            )
        ]
        self._cache_match_result(
            tokens=tokens,
            char_type=char_type,
            query_text=query_text,
            result=result,
        )
        return result

    def match_chars_by_pinyin(
        self,
        pinyin: str,
        *,
        char_type: CharMatchType = "char",
        query_text: str | None = None,
    ) -> list[str]:
        """根据拼音匹配普通汉字或姓氏候选字。

        ✅ **支持多字输入**：拼音可通过空格/逗号/分号分隔多个 token。

        处理流程：
        1. 将用户输入拆分为多个拼音 token（如 "li ming" → ["li", "ming"]）
        2. 对每个 token 进行模糊匹配，获取匹配的 key
        3. 从 mapping 中获取候选字，计算排序 rank
        4. 合并多个 token 的候选，保留每个字的最优 rank
        5. 按 rank 排序返回

        Args:
            pinyin: 用户输入的拼音（支持多 token，空格/逗号/分号分隔）
            char_type: "char" 普通汉字，"surname" 姓氏
            query_text: 原始查询文本，用于精确匹配加权

        Returns:
            排序后的候选字列表

        Example:
            >>> matcher.match_chars_by_pinyin("li")
            ['李', '理', '里', '力', ...]
            >>> matcher.match_chars_by_pinyin("li ming")  # 多字支持
            ['李', '明', '理', '鸣', ...]
        """
        return self._match_chars_by_tokens(
            tuple(self.split_pinyin_query(pinyin)),
            char_type=char_type,
            query_text=query_text,
        )

    def match_chars_by_text(
        self,
        text: str,
        *,
        char_type: CharMatchType = "char",
    ) -> list[str]:
        """根据文本自身可能的读音匹配候选字。"""
        normalized_text = text.strip()
        if not normalized_text:
            return []

        return self._match_chars_by_tokens(
            self.resolve_text_to_pinyin_keys(normalized_text, char_type=char_type),
            char_type=char_type,
            query_text=normalized_text,
        )

    def match_surname_chars_by_text(self, text: str) -> list[str]:
        """根据中文姓氏文本匹配候选姓氏。"""
        return self.match_chars_by_text(text, char_type="surname")


__all__ = ["PinyinCharsMatcher"]
