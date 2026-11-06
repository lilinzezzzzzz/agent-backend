"""FastAPI 异常处理与 HTTP 200 错误信封的遥测记录。"""

import time

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from opentelemetry import _logs, trace
from opentelemetry.trace import StatusCode

from internal.core import AppException, errors
from pkg.api_response import error_response
from pkg.logger import logger
from pkg.toolkit.exc import format_exception_traceback


def exception_response(exc: Exception, *, logs_enabled: bool = False) -> Response:
    """返回 HTTP 200 错误信封，并记录框架无法从响应状态判断的失败。"""
    # 信封可能使用 HTTP 200，不能仅依赖框架根据 HTTP status 判断请求是否失败。
    request_span = trace.get_current_span()
    if request_span.is_recording():
        request_span.record_exception(exc)
        request_span.set_status(StatusCode.ERROR, type(exc).__name__)

    if isinstance(exc, AppException):
        logger.warning(
            f"Business exception, exc={format_exception_traceback(exc, max_entries=3)}"
        )
        return error_response(error=exc.error, message=exc.message)

    if isinstance(exc, RequestValidationError):
        # FastAPI 已在校验失败处生成原生日志，这里只保留应用日志和响应约定。
        logger.warning(f"Validation Error: {exc}")
        return error_response(
            error=errors.BadRequest, message=f"Validation Error: {exc}"
        )

    logger.error(
        f"Unexpected exception, exc={format_exception_traceback(exc, max_entries=10)}"
    )
    if logs_enabled:
        # 异常已被 recorder 处理，框架外层观察器不会收到，需要在此记录。
        try:
            _logs.get_logger(
                __name__,
                logger_provider=_logs.get_logger_provider(),
            ).emit(
                exception=exc,
                event_name="http.server.request.exception",
                timestamp=time.time_ns(),
                severity_number=_logs.SeverityNumber.ERROR,
                severity_text="ERROR",
                body="Unhandled exception in request",
            )
        except Exception:
            logger.opt(exception=True).warning("Failed to emit OTel exception log")
    return error_response(error=errors.InternalServerError, message=str(exc))


async def request_exception_handler(_request: Request, exc: Exception) -> Response:
    """通过 FastAPI exception handler 处理业务错误和请求校验错误。"""
    return exception_response(exc)
