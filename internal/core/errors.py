"""全局错误码定义"""

from typing import Any

from pkg.toolkit.response import AppError


class GlobalErrors:
    """
    全局状态码定义
    """

    # 客户端错误 (40000 - 49999)
    BadRequest = AppError(40000, {"zh": "请求参数错误", "en": "Bad Request"})
    Unauthorized = AppError(40001, {"zh": "未授权，请登录", "en": "Unauthorized"})
    InvalidSignature = AppError(
        40002, {"zh": "签名验证失败", "en": "Signature Invalid"}
    )
    Forbidden = AppError(40003, {"zh": "权限不足，禁止访问", "en": "Forbidden"})
    NotFound = AppError(40004, {"zh": "资源不存在", "en": "Not Found"})
    PayloadTooLarge = AppError(40005, {"zh": "请求载荷过大", "en": "Payload Too Large"})
    UnprocessableEntity = AppError(
        40006, {"zh": "无法处理的实体", "en": "Unprocessable Entity"}
    )
    EndpointDisabled = AppError(40007, {"zh": "接口已禁用", "en": "Endpoint Disabled"})
    StreamTimeout = AppError(40800, {"zh": "流超时", "en": "Stream Timeout"})

    # 服务端错误 (50000 - 59999)
    InternalServerError = AppError(
        50000, {"zh": "服务器内部错误", "en": "Internal Server Error"}
    )
    StreamError = AppError(50001, {"zh": "流处理错误", "en": "Stream Error"})
    ServiceUnavailable = AppError(
        50300, {"zh": "服务暂不可用", "en": "Service Unavailable"}
    )


errors = GlobalErrors()


# =========================================================
# 流处理异常
# =========================================================


class StreamTimeoutError(Exception):
    """流超时异常

    当流中的 chunk 生成超时时抛出。
    """

    def __init__(
        self, message: str, timeout: float, context: dict[str, Any] | None = None
    ):
        super().__init__(message)
        self.timeout = timeout
        self.context = context or {}


class StreamError(Exception):
    """流处理异常

    当流处理过程中发生错误时抛出。
    """

    def __init__(
        self,
        message: str,
        original_error: Exception,
        context: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.original_error = original_error
        self.context = context or {}
