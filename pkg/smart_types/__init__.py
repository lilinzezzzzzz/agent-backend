from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer, WithJsonSchema

from pkg.toolkit.float import is_safe_float_range
from pkg.toolkit.timer import format_iso_datetime, parse_iso_string

# JavaScript 安全整数最大值 (2^53 - 1)
JS_MAX_SAFE_INTEGER = 9007199254740991


# ==========================================
# 1. SmartInt (智能整数)
# ==========================================


def _parse_smart_int(v: Any) -> int:
    """
    [输入处理]
    前端传 "123" 或 123，后端统一转为 int 类型，
    以便 Python 内部进行数学运算或数据库存储。
    仅支持整数或纯数字字符串，其他类型返回错误。
    不接受 bool 类型。
    """
    if isinstance(v, bool):
        raise ValueError("Expected int or str, got bool")
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v)
        except ValueError as e:
            raise ValueError(f"Invalid integer value: {v}") from e
    raise ValueError(f"Expected int or str, got {type(v).__name__}, {v}")


def _serialize_smart_int(v: int) -> int | str:
    """
    [输出处理]
    序列化给前端时：
    - 如果数值超过 JS 安全范围，转为字符串 (保护精度)
    - 否则保留为数字 (方便前端计算)
    """
    if abs(v) > JS_MAX_SAFE_INTEGER:
        return str(v)
    return v


SmartInt = Annotated[
    int,  # Python 内部类型始终是 int
    BeforeValidator(_parse_smart_int),
    PlainSerializer(_serialize_smart_int, return_type=int | str, when_used="json"),
    WithJsonSchema(
        {
            "anyOf": [{"type": "integer"}, {"type": "string"}],
            "title": "SmartInt",
            "description": "Int in Python. Auto-converts to string in JSON if > JS safe range.",
            "example": 12345,
        }
    ),
]


# ==========================================
# 2. SmartDecimal (智能浮点数)
# ==========================================


def _parse_smart_decimal(v: Any) -> Decimal:
    """
    [输入处理]
    前端传 string/float/int，后端统一转为 Decimal 以保证计算精度。
    仅支持 Decimal、int、float、str 类型，其他类型返回错误。
    不接受 bool 类型。
    """
    if isinstance(v, bool):
        raise ValueError("Expected Decimal, int, float or str, got bool")
    if isinstance(v, Decimal):
        return v
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, float):
        return Decimal(str(v))
    if isinstance(v, str):
        try:
            return Decimal(v)
        except InvalidOperation as e:
            raise ValueError(f"Invalid decimal value: {v}") from e
    raise ValueError(f"Expected Decimal, int, float or str, got {type(v).__name__}")


def _serialize_smart_decimal(v: Decimal) -> float | str:
    """
    [输出处理]
    - 范围在 -1e15 到 1e15 且小数位 <= 6 位 -> 转 float (前端友好)
    - 否则 -> 转 str (保留高精度)
    """
    # 获取指数 (decimal 的 exponent 为负数表示小数位，例如 0.01 是 -2)
    # 注意：Decimal(10).as_tuple().exponent 是 0
    if is_safe_float_range(v):
        return float(v)
    return str(v)


SmartDecimal = Annotated[
    Decimal,  # Python 内部类型始终是 Decimal
    BeforeValidator(_parse_smart_decimal),
    PlainSerializer(
        _serialize_smart_decimal, return_type=float | str, when_used="json"
    ),
    WithJsonSchema(
        {
            "anyOf": [{"type": "number"}, {"type": "string"}],
            "title": "SmartDecimal",
            "description": "Decimal in Python. Float in JSON if simple, String if high precision.",
            "example": 12.3456,
        }
    ),
]


# ==========================================
# 3. SmartDatetime (智能时间类型)
# ==========================================


def _parse_smart_datetime(v: Any) -> datetime:
    """
    [输入处理] Input -> Python (Naive UTC datetime)
    1. 接收 ISO 格式字符串 (如 "2023-01-01T12:00:00Z" 或 "2023-01-01T20:00:00+08:00")
    2. 接收 datetime 对象
    3. 统一转换为 UTC 时间，再去除时区信息 (转为 Naive datetime)
    """
    if isinstance(v, datetime):
        # 如果有时区信息，先转换到 UTC，再去除时区信息
        if v.tzinfo is not None:
            v = v.astimezone(UTC)
        return v.replace(tzinfo=None)

    if isinstance(v, str):
        try:
            dt = parse_iso_string(v)
            # 转换为 UTC 并去除时区信息
            return dt.astimezone(UTC).replace(tzinfo=None)
        except ValueError as e:
            raise ValueError(f"Invalid ISO 8601 datetime string: {v}") from e

    raise ValueError(f"Invalid datetime type: {type(v)}")


def _serialize_smart_datetime(v: datetime) -> str:
    """
    [输出处理] Python -> JSON (ISO String)
    将 datetime 对象转为标准的 ISO 8601 字符串格式（UTC 时间）
    - 如果是 naive datetime（无时区），默认视为 UTC
    - 如果带有时区信息，先转换为 UTC
    - 输出使用 'Z' 格式表示 UTC
    """
    return format_iso_datetime(v, use_z=True, timespec="seconds")


SmartDatetime = Annotated[
    datetime,  # <--- Python 内部类型明确为 datetime
    BeforeValidator(_parse_smart_datetime),
    PlainSerializer(
        _serialize_smart_datetime, return_type=str, when_used="json-unless-none"
    ),
    WithJsonSchema(
        {
            "type": "string",
            "format": "date-time",
            "example": "2025-05-07T14:30:00",
            "title": "SmartDatetime",
            "description": "Auto-converts ISO string to Naive datetime on input; Serializes to ISO string on output.",
        }
    ),
]
# ==========================================
# 4. IntStr (强制字符串 ID)
# ==========================================
# 如果你的 Python 内部业务逻辑确实需要它就是 string，保持原样即可。
# 如果 Python 内部需要 int，但输出需要 string，建议用类似 SmartInt 的逻辑但 serializer 恒定返回 str。


def _parse_int_str(v: Any) -> str:
    """
    [输入处理]
    仅接受 int 或 str 类型，不接受 bool、None、list、dict 等。
    """
    if isinstance(v, bool):
        raise ValueError("Expected int or str, got bool")
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return v
    raise ValueError(f"Expected int or str, got {type(v).__name__}")


IntStr = Annotated[
    str,
    BeforeValidator(_parse_int_str),
    WithJsonSchema(
        {
            "type": "string",
            "title": "IntStr",
            "description": "Force serialization to string",
            "example": "115603251198457884",
        }
    ),
]
