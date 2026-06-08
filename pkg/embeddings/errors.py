class EmbeddingCoreError(Exception):
    """Embedding module base error."""


class InvalidEmbeddingDimensionError(EmbeddingCoreError):
    """Embedding vector dimension mismatch."""


class EmbeddingResponseValidationError(EmbeddingCoreError):
    """Embedding provider response is invalid."""
