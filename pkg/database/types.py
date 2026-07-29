from typing import Any

from sqlalchemy import Text
from sqlalchemy.dialects import oracle, postgresql, sqlite
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.mutable import Mutable, MutableDict, MutableList
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.types import JSON as SA_JSON, TypeDecorator

from pkg.toolkit.json import orjson_dumps, orjson_loads

ColumnKey = str | InstrumentedAttribute
"""列键类型：可以是字符串列名或 InstrumentedAttribute"""


class JSONType(TypeDecorator):
    """
    跨数据库兼容的 JSON 类型，自动适配不同数据库的最优存储方式。

    支持的数据库:
        - PostgreSQL: JSONB（支持索引、JSON 路径查询）
        - MySQL 5.7+: 原生 JSON
        - SQLite: JSON（SQLAlchemy 方言支持）
        - Oracle 21c+: 原生 JSON（默认模式）
        - Oracle 12c-20c: CLOB + 手动序列化（需设置 oracle_native_json=False）
        - 其他数据库: TEXT + 手动序列化

    用法示例:
        from pkg.database import JSONType, ModelMixin
        from sqlalchemy.orm import Mapped, mapped_column

        class MyModel(ModelMixin):
            __tablename__ = "my_table"

            # 基础用法（自动适配数据库）
            config: Mapped[dict] = mapped_column(JSONType(), default=dict)
            tags: Mapped[list] = mapped_column(JSONType(), default=list)

            # Oracle 12c-20c CLOB 模式
            metadata_: Mapped[dict] = mapped_column(
                "metadata", JSONType(oracle_native_json=False), default=dict
            )

            # 可空 JSON 字段
            extra: Mapped[dict | None] = mapped_column(
                JSONType(none_as_null=True), nullable=True
            )

    Args:
        oracle_native_json: Oracle 是否使用原生 JSON 类型
            - True（默认）: 使用原生 JSON，仅支持 Oracle 21c+，性能更好
            - False: 使用 CLOB 存储，兼容 Oracle 12c+
        none_as_null: Python None 是否存储为 SQL NULL
            - False（默认）: 存储为 JSON null
            - True: 存储为 SQL NULL

    注意事项:
        1. Oracle 版本兼容性:
           - 21c+ 使用默认的原生 JSON 模式即可
           - 12c-20c 必须设置 oracle_native_json=False 使用 CLOB 模式
        2. 序列化行为:
           - PostgreSQL/MySQL/SQLite/Oracle 原生 JSON: SQLAlchemy 和驱动处理
           - Oracle CLOB/其他数据库: 使用 orjson 序列化
        3. 空值处理:
           - 默认将 Python None 存为 JSON null
           - 需要 SQL NULL 语义时显式使用 JSONType(none_as_null=True)
           - 空字符串 "" 读取时返回 None
        4. 容错机制:
           - MySQL/SQLite 会兼容读取历史双重序列化数据
           - 读取非 JSON 格式数据时不会抛异常，返回原始值
           - Oracle LOB 对象会自动调用 read() 获取内容
    """

    # 默认底层使用 Text，但在 PG/MySQL/OracleNative 下会被 load_dialect_impl 覆盖
    impl = Text
    cache_ok = True

    def __init__(
        self,
        oracle_native_json: bool = True,
        *,
        none_as_null: bool = False,
    ) -> None:
        super().__init__()
        self.oracle_native_json = oracle_native_json
        self.none_as_null = none_as_null

    @property
    def should_evaluate_none(self) -> bool:
        """保持 ORM 对 None 的省略行为与 SQLAlchemy JSON 类型一致。"""
        return not self.none_as_null

    @property
    def python_type(self):
        """
        告诉 SQLAlchemy 这个类型在 Python 侧对应 dict/list。
        返回 object 以兼容两种情况，避免类型检查的歧义。
        """
        return object

    def load_dialect_impl(self, dialect: Dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(
                postgresql.JSONB(none_as_null=self.none_as_null)
            )
        elif dialect.name == "mysql":
            return dialect.type_descriptor(SA_JSON(none_as_null=self.none_as_null))
        elif dialect.name == "sqlite":
            # SQLite 使用方言特定的 JSON 类型，以便 SA 能够识别
            return dialect.type_descriptor(sqlite.JSON(none_as_null=self.none_as_null))
        elif dialect.name == "oracle":
            if self.oracle_native_json:
                # 使用 getattr 避免旧版 SA 报错
                oracle_json_type = getattr(oracle, "JSON", SA_JSON)
                return dialect.type_descriptor(
                    oracle_json_type(none_as_null=self.none_as_null)
                )
            else:
                return dialect.type_descriptor(oracle.CLOB())
        else:
            return dialect.type_descriptor(Text())

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        # 原生 JSON 类型负责序列化和 none_as_null 语义，避免双重序列化。
        if dialect.name in {"postgresql", "mysql", "sqlite"} or (
            dialect.name == "oracle" and self.oracle_native_json
        ):
            return value

        # CLOB/TEXT 没有 JSON 类型处理器，需要在此统一空值语义。
        if value is None and self.none_as_null:
            return None

        if value is SA_JSON.NULL:
            return "null"

        # 避免双重序列化：如果已经是字符串，则不再次 dumps
        if isinstance(value, (str, bytes)):
            return value

        # Oracle CLOB/其他 TEXT：手动序列化。
        return orjson_dumps(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None

        # 1. 已经是 dict/list，直接返回（驱动已处理）
        if isinstance(value, (dict, list)):
            return value

        # 2. PostgreSQL JSONB 驱动会自动反序列化
        if dialect.name == "postgresql":
            return value

        # 3. Oracle 原生 JSON 模式（21c+）
        if dialect.name == "oracle" and self.oracle_native_json:
            return value

        # 4. 处理 Oracle LOB 对象
        if hasattr(value, "read"):
            value = value.read()

        # 5. 空字符串处理
        if isinstance(value, str) and not value.strip():
            return None

        # 6. 反序列化（MySQL/SQLite/Oracle CLOB/其他）
        try:
            return orjson_loads(value)
        except (TypeError, ValueError):
            # 容错：如果数据库里存了非 JSON 的纯文本，避免整个查询崩溃
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
