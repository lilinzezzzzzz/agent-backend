class OpenAIClientError(RuntimeError):
    """Base exception raised by OpenAIClient helper methods."""


class StructuredOutputRefusalError(OpenAIClientError):
    """Raised when a model refuses a structured output request."""


class StructuredOutputParseError(OpenAIClientError):
    """Raised when a structured output response cannot be parsed."""
