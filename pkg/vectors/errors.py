from pkg.embeddings.errors import InvalidEmbeddingDimensionError as InvalidEmbeddingDimensionError


class VectorCoreError(Exception):
    """向量核心模块基类异常。"""


class CollectionSchemaMismatchError(VectorCoreError):
    """Collection schema 与声明的 spec 不匹配。"""


class CapabilityNotSupportedError(VectorCoreError):
    """当前 backend 不支持请求的能力。"""


class UnsupportedFilterError(VectorCoreError):
    """当前 backend 无法翻译过滤条件。"""


class RecordValidationError(VectorCoreError):
    """向量记录不满足写入约束。"""
