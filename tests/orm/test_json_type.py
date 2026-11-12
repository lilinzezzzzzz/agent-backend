"""
JSONType 跨数据库兼容 JSON 类型测试

测试内容:
    1. 方言适配 (load_dialect_impl)
    2. 序列化/反序列化 (process_bind_param / process_result_value)
    3. MutableDict/MutableList 变更追踪
    4. 边缘情况处理
"""

from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import JSON as SA_JSON, String, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

from pkg.database import Base, BaseDao, JSONType, ModelMixin, new_async_session_maker
from pkg.toolkit.json import orjson_loads


# ==========================================
# 1. 测试模型定义
# ==========================================
class JsonModel(ModelMixin):
    __tablename__ = "json_test"

    name: Mapped[str] = mapped_column(String(50))
    config: Mapped[dict] = mapped_column(JSONType(), default=dict)
    tags: Mapped[list] = mapped_column(JSONType(), default=list)
    extra: Mapped[dict | None] = mapped_column(JSONType(), nullable=True)
    extra_sql_null: Mapped[dict | None] = mapped_column(
        JSONType(none_as_null=True), nullable=True
    )


class JsonModelDao(BaseDao[JsonModel]):
    _model_cls = JsonModel


async def persist_json_model(db_session, model: JsonModel) -> None:
    await JsonModelDao(session_provider=db_session).insert(model)


@pytest.mark.parametrize("dialect", [postgresql.dialect(), sqlite.dialect()])
@pytest.mark.parametrize("none_as_null", [False, True])
def test_json_dialect_and_null_policy(dialect, none_as_null: bool) -> None:
    json_type = JSONType(none_as_null=none_as_null)
    impl = json_type.load_dialect_impl(dialect)
    assert impl.none_as_null is none_as_null
    assert json_type.should_evaluate_none is not none_as_null
    assert isinstance(
        impl, postgresql.JSONB if dialect.name == "postgresql" else sqlite.JSON
    )
    processor = json_type.dialect_impl(dialect).bind_processor(dialect)
    assert processor is not None
    assert processor(None) == (None if none_as_null else "null")
    assert processor(SA_JSON.NULL) == "null"


@pytest.mark.parametrize("dialect", [postgresql.dialect(), sqlite.dialect()])
def test_native_json_serializes_once(dialect) -> None:
    value = {"nested": {"number": 1}, "items": [True, None]}
    json_type = JSONType()
    assert json_type.process_bind_param(value, dialect) is value
    processor = json_type.dialect_impl(dialect).bind_processor(dialect)
    assert processor is not None
    assert orjson_loads(processor(value)) == value


@pytest.mark.parametrize("name", ["mysql", "mariadb", "oracle", "mssql", "unknown"])
def test_unsupported_dialects_are_rejected(name: str) -> None:
    dialect = MagicMock(name=name)
    dialect.name = name
    with pytest.raises(NotImplementedError, match="PostgreSQL and SQLite only"):
        JSONType().load_dialect_impl(dialect)


@pytest.mark.parametrize("value", [None, {"key": 1}, [1, 2], "text", 1, True])
def test_postgresql_driver_values_are_preserved(value) -> None:
    assert JSONType().process_result_value(value, postgresql.dialect()) == value


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ({"key": 1}, {"key": 1}),
        ([1, 2], [1, 2]),
        ('{"key": 1}', {"key": 1}),
        ("[1, 2]", [1, 2]),
        ("", None),
        ("  ", None),
        ("not-json", "not-json"),
        (1, 1),
    ],
)
def test_sqlite_preserves_legacy_json_read_behavior(value, expected) -> None:
    assert JSONType().process_result_value(value, sqlite.dialect()) == expected


@pytest_asyncio.fixture(loop_scope="function")
async def db_session():
    """创建测试数据库会话"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = new_async_session_maker(engine)
    yield session_maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_json_crud(db_session):
    """测试 JSON 字段的完整 CRUD 流程"""
    # Create
    model = JsonModel.create(
        name="test",
        config={"debug": True, "version": "1.0"},
        tags=["python", "fastapi"],
    )
    await persist_json_model(db_session, model)

    # Read
    async with db_session() as session:
        result = await session.get(JsonModel, model.id)
        assert result.config == {"debug": True, "version": "1.0"}
        assert result.tags == ["python", "fastapi"]
        assert result.extra is None


@pytest.mark.asyncio
async def test_json_mutable_tracking(db_session):
    """测试 MutableDict/MutableList 变更追踪"""
    # 创建并保存
    model = JsonModel.create(name="mutable_test", config={"count": 0}, tags=[])
    await persist_json_model(db_session, model)

    # 修改 JSON 字段
    async with db_session() as session:
        async with session.begin():
            result = await session.get(JsonModel, model.id)
            result.config["count"] = 1  # MutableDict 追踪
            result.config["new_key"] = "added"
            result.tags.append("new_tag")  # MutableList 追踪

    # 验证变更已持久化
    async with db_session() as session:
        result = await session.get(JsonModel, model.id)
        assert result.config["count"] == 1
        assert result.config["new_key"] == "added"
        assert "new_tag" in result.tags


@pytest.mark.asyncio
async def test_json_nullable_field(db_session):
    """测试 JSON null 和 SQL NULL 的存储语义。"""
    # 测试 None 值
    model1 = JsonModel.create(name="nullable_none", extra=None)
    await persist_json_model(db_session, model1)

    async with db_session() as session:
        result = await session.get(JsonModel, model1.id)
        assert result.extra is None
        assert result.extra_sql_null is None

        storage_types = (
            await session.execute(
                text(
                    "SELECT typeof(extra), json_type(extra), "
                    "typeof(extra_sql_null), json_type(extra_sql_null) "
                    "FROM json_test WHERE id = :id"
                ),
                {"id": model1.id.hex},
            )
        ).one()
        assert storage_types == ("text", "null", "null", None)

    # 测试有值
    model2 = JsonModel.create(name="nullable_with_value", extra={"meta": "data"})
    await persist_json_model(db_session, model2)

    async with db_session() as session:
        result = await session.get(JsonModel, model2.id)
        assert result.extra == {"meta": "data"}


@pytest.mark.asyncio
async def test_json_complex_nested(db_session):
    """测试复杂嵌套 JSON 结构"""
    complex_config = {
        "database": {
            "host": "localhost",
            "port": 5432,
            "options": {"timeout": 30, "pool_size": 10},
        },
        "features": ["auth", "cache", "logging"],
        "enabled": True,
        "ratio": 0.95,
    }

    model = JsonModel.create(name="complex", config=complex_config)
    await persist_json_model(db_session, model)

    async with db_session() as session:
        result = await session.get(JsonModel, model.id)
        assert result.config["database"]["port"] == 5432
        assert result.config["database"]["options"]["timeout"] == 30
        assert "cache" in result.config["features"]
        assert result.config["ratio"] == 0.95
