class OpenAIClientError(RuntimeError):
    """Base exception raised by OpenAI client helper methods."""


class StructuredOutputRefusalError(OpenAIClientError):
    """Raised when a model refuses a structured output request."""


class StructuredOutputParseError(OpenAIClientError):
    """Raised when a structured output response cannot be parsed."""


class ResponseIncompleteError(OpenAIClientError):
    """响应未完成，不能作为完整业务结果使用。"""


class ResponseFailedError(OpenAIClientError):
    """模型响应或事件流失败。"""
