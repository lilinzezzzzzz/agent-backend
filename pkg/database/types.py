from typing import Any

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.mutable import Mutable, MutableDict, MutableList
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.types import JSON as SA_JSON, TypeDecorator, TypeEngine

from pkg.toolkit.json import orjson_loads

ColumnKey = str | InstrumentedAttribute
"""列键类型：可以是字符串列名或 InstrumentedAttribute。"""


class JSONType(TypeDecorator):
    """PostgreSQL JSONB / SQLite JSON，保留可变对象追踪与空值语义。

    ``none_as_null=False`` 将 Python None 存为 JSON null；True 使用 SQL NULL。
    序列化由原生 JSON 类型执行，SQLite 读取时兼容历史双重编码数据；
    其他数据库方言明确拒绝，不降级成文本存储。
    """

    impl = SA_JSON
    cache_ok = True

    def __init__(self, *, none_as_null: bool = False) -> None:
        super().__init__()
        self.none_as_null = none_as_null

    @property
    def should_evaluate_none(self) -> bool:
        return not self.none_as_null

    @property
    def python_type(self) -> type[object]:
        return object

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(
                postgresql.JSONB(none_as_null=self.none_as_null)
            )
        if dialect.name == "sqlite":
            return dialect.type_descriptor(sqlite.JSON(none_as_null=self.none_as_null))
        raise NotImplementedError(
            f"JSONType supports PostgreSQL and SQLite only, got {dialect.name}"
        )

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        return value

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None or isinstance(value, (dict, list)):
            return value
        if dialect.name == "postgresql":
            return value
        if isinstance(value, str) and not value.strip():
            return None
        try:
            return orjson_loads(value)
        except TypeError, ValueError:
            return value


class MutableJSON(Mutable):
    """
    智能 JSON 变更追踪器。
    能够自动识别 dict 和 list，并分别委托给 MutableDict 或 MutableList 处理。
    """

    @classmethod
    def coerce(cls, key: str, value: Any) -> Any:
        if value is None:
            return None

        # 1. 如果已经是具备追踪能力的 Mutable 对象，直接返回
        if isinstance(value, (MutableDict, MutableList)):
            return value

        # 2. 如果是字典，委托给 MutableDict
        if isinstance(value, dict):
            return MutableDict.coerce(key, value)

        # 3. 如果是列表，委托给 MutableList
        if isinstance(value, list):
            return MutableList.coerce(key, value)

        # 4. 其他类型（如 int, str 等），无法追踪内部变更，直接返回
        return value


# ==========================================================================
# 重要：为 JSONType 注册变更追踪
# ==========================================================================
# 这样操作 model.config['key'] = 'value' 时，SA 才能感知到变化并执行 UPDATE
# 注意：MutableDict 只能追踪顶层 key 的变化，深层嵌套修改仍需手动 flag_modified


MutableJSON.associate_with(JSONType)
