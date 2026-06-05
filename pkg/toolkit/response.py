from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from starlette.responses import JSONResponse

from pkg.toolkit.json import orjson_dumps

# =========================================================
# 1. 定义状态码结构与全局状态码
# =========================================================


@dataclass(frozen=True)
class AppStatus:
    """
    应用状态对象基类
    将状态码与多语言文案绑定在一起
    """

    code: int
    message: dict[str, str]

    def get_message(self, lang: str = "zh") -> str:
        """根据语言获取文案，默认回退到中文"""
        return self.message.get(lang) or self.message["zh"]


success_status = AppStatus(20000, {"zh": "", "en": "success"})


@dataclass(frozen=True)
class AppError(AppStatus):
    """
    专门用于表示应用错误的子类 (继承自 AppStatus)
    """

    def __repr__(self) -> str:
        return f"(code={self.code}, message={self.message})"


# =========================================================
# 2. 响应载荷
# =========================================================


class ResponsePayload(BaseModel):
    """
    统一响应体结构。

    正常 Controller 返回该模型或同结构 dict，让 FastAPI 的 response_model
    负责最终响应校验、字段过滤和 JSON 序列化。
    """

    code: int = 20000
    message: str = ""
    data: Any = None


# =========================================================
# 3. 响应构造
# =========================================================


def _make_payload(*, code: int, data: Any = None, message: str = "") -> ResponsePayload:
    """基础响应载荷构造器。"""
    return ResponsePayload(code=code, message=message, data=data)


def _process_success_data(
    data: dict | list | BaseModel | None = None,
) -> dict | list | None:
    """
    验证成功响应的数据类型，并展开 Pydantic 模型。

    Args:
        data: 传入的响应数据。

    Returns:
        处理后的 dict、list 或 None。

    Raises:
        TypeError: 如果数据类型不符合要求。
    """
    if data is None:
        return data

    if isinstance(data, BaseModel):
        data = data.model_dump(mode="python")
    elif isinstance(data, list):
        data = [
            item.model_dump(mode="python") if isinstance(item, BaseModel) else item
            for item in data
        ]
    elif not isinstance(data, dict):
        raise TypeError(
            f"Success response data must be a dict, list, a Pydantic model instance, or None, but received type: {type(data)}"
        )

    return data


def _build_error_body(
    error: AppError, *, message: str | None = "", lang: str = "zh"
) -> ResponsePayload:
    """
    构造错误响应载荷。

    Args:
        error: GlobalCodes 中定义的错误对象
        message: 自定义详细信息。如果传入，将拼接到默认文案后面。
        lang: 语言代码 ('zh', 'en')，默认为 'zh'
    """
    base_msg = error.get_message(lang)
    final_message = f"{base_msg}: {message}" if message else base_msg
    return _make_payload(code=error.code, message=final_message)


# =========================================================
# 4. 工具函数
# =========================================================


def success_response(data: dict | list | BaseModel | None = None) -> ResponsePayload:
    """
    成功响应
    """
    processed_data = _process_success_data(data)
    return _make_payload(code=success_status.code, data=processed_data)


def success_list_response(
    data: list, page: int, limit: int, total: int
) -> ResponsePayload:
    """
    分页列表响应
    """
    if not isinstance(data, list):
        raise TypeError("Items must be a list")

    processed_items = _process_success_data(data)
    return _make_payload(
        code=success_status.code,
        data={"items": processed_items, "page": page, "limit": limit, "total": total},
    )


def error_response(
    error: AppError, *, message: str | None = "", lang: str = "zh"
) -> JSONResponse:
    """
    通用错误 HTTP 响应
    """
    response_body = _build_error_body(error=error, message=message, lang=lang)
    return JSONResponse(content=response_body.model_dump(mode="json"))


def wrap_sse_data(content: str | dict) -> str:
    """
    将内容包装为 SSE (Server-Sent Events) 格式
    """
    if isinstance(content, dict):
        # 序列化并确保是 utf-8 字符串
        content = orjson_dumps(content)
    return f"data: {content}\n\n"


def wrap_sse_event(event: str, data: str | dict) -> str:
    """
    将事件名和内容包装为 SSE (Server-Sent Events) 格式。
    """
    if isinstance(data, dict):
        data = orjson_dumps(data)
    return f"event: {event}\ndata: {data}\n\n"


'''
使用示例
class GlobalCodes(BaseCodes):
    """
    全局状态码定义
    """

    # 客户端错误 (40000 - 49999)
    BadRequest = AppError(40000, {"zh": "请求参数错误", "en": "Bad Request"})
    Unauthorized = AppError(40001, {"zh": "未授权，请登录", "en": "Unauthorized"})
    Forbidden = AppError(40003, {"zh": "权限不足，禁止访问", "en": "Forbidden"})
    NotFound = AppError(40004, {"zh": "资源不存在", "en": "Not Found"})
    PayloadTooLarge = AppError(40005, {"zh": "请求载荷过大", "en": "Payload Too Large"})
    UnprocessableEntity = AppError(40006, {"zh": "无法处理的实体", "en": "Unprocessable Entity"})

    # 服务端错误 (50000 - 59999)
    InternalServerError = AppError(50000, {"zh": "服务器内部错误", "en": "Internal Server Error"})


global_codes = BaseCodes
'''
