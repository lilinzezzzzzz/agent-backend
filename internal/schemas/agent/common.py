"""Agent JSON 值类型与序列化辅助。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

type JsonValue = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)


def to_json_value(value: object) -> JsonValue:
    """把任意动作结果压缩为可响应的 JSON 值。"""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [to_json_value(item) for item in value]
    return str(value)


def to_json_object(value: object) -> dict[str, JsonValue]:
    """把任意 mapping 压缩为 JSON object；非 mapping 返回空对象。"""
    if not isinstance(value, Mapping):
        return {}
    return {str(key): to_json_value(item) for key, item in value.items()}


def _with_optional_fields(
    data: dict[str, JsonValue], **fields: str | None
) -> dict[str, JsonValue]:
    for key, value in fields.items():
        if value is not None:
            data[key] = value
    return data
