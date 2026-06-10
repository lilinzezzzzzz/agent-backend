from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self

from sqlalchemy import (
    BigInteger,
    DateTime,
    Executable,
    Insert,
    Update,
    insert,
    inspect,
    update,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute, Mapped, mapped_column

from pkg.database.types import ColumnKey
from pkg import request_context as context
from pkg.ids import snowflake_id_generator
from pkg.toolkit.json import JsonInputType, orjson_dumps, orjson_loads
from pkg.toolkit.timer import utc_now_naive

SessionProvider = Callable[..., AbstractAsyncContextManager[AsyncSession]]


def new_async_engine(
    *,
    database_uri: str,
    echo: bool = True,
    pool_pre_ping: bool = True,
    pool_size: int = 10,
    max_overflow: int = 20,
    pool_timeout: int = 30,
    pool_recycle: int = 1800,
    json_serializer: Callable[[Any], str] = orjson_dumps,
    json_deserializer: Callable[[JsonInputType], Any] = orjson_loads,
) -> AsyncEngine:
    return create_async_engine(
        url=database_uri,
        echo=echo,
        pool_pre_ping=pool_pre_ping,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_recycle=pool_recycle,
        json_serializer=json_serializer,
        json_deserializer=json_deserializer,
    )


def new_async_session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=True
    )


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 声明式基类"""

    pass


@dataclass(frozen=True, slots=True)
class ContextDefaults:
    now: datetime
    user_id: int | None


class ModelMixin(Base):
    """
    通用模型 Mixin
    """

    __abstract__ = True

    # --- 字段定义 ---
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    creator_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))

    updater_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), default=None
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), default=None
    )

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

    # ==========================================================================
    # 工厂方法
    # ==========================================================================

    @classmethod
    def create(cls, **kwargs) -> Self:
        """
        创建一个新的、填充好默认值的实例（Transient 状态）。
        """
        valid_cols = set(cls.get_column_names())
        clean_kwargs = {k: v for k, v in kwargs.items() if k in valid_cols}

        ins = cls(**clean_kwargs)
        ins.fill_ins_insert_fields()
        return ins

    # ==========================================================================
    # 单例写操作（仅构造语句，不直接执行）
    # ==========================================================================

    def build_insert_stmt(self) -> Insert:
        """[Strict Insert] 构造新对象的 INSERT 语句。"""
        state = inspect(self)

        if not state.transient:
            raise RuntimeError(
                f"build_insert_stmt() is strictly for INSERT operations. "
                f"Object {self.__class__.__name__}(id={self.id}) is already persistent/detached. "
                f"Please use build_update_stmt() instead."
            )

        return insert(self.__class__).values(self.prepare_insert_values())

    def prepare_insert_values(self) -> dict[str, Any]:
        """补全实例插入字段并返回可用于 INSERT 的列值。"""
        self.fill_ins_insert_fields()
        return self.extract_db_values()

    @staticmethod
    def normalize_update_column_name(column: ColumnKey) -> str:
        if isinstance(column, InstrumentedAttribute):
            return column.key
        return column

    def apply_updates(
        self,
        updates: Mapping[ColumnKey, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """更新实例字段并返回可用于 UPDATE 的列值。"""
        state = inspect(self)

        if state.transient:
            raise RuntimeError(
                f"build_update_stmt() is strictly for UPDATE operations on existing records. "
                f"Object {self.__class__.__name__} is new (transient). "
                f"Please use build_insert_stmt() first."
            )

        data: dict[str, Any] = {}
        raw_updates = dict(updates or {})
        raw_updates |= kwargs

        for key, value in raw_updates.items():
            column_name = self.normalize_update_column_name(key)
            if self.has_column(column_name):
                setattr(self, column_name, value)
                data[column_name] = value

        self.fill_ins_update_fields(data)
        return data

    def build_update_stmt(
        self,
        updates: Mapping[ColumnKey, Any] | None = None,
        **kwargs: Any,
    ) -> Update:
        """[Strict Update] 构造已存在对象的 UPDATE 语句，并同步实例字段。"""
        data = self.apply_updates(updates=updates, **kwargs)
        return update(self.__class__).where(self.__class__.id == self.id).values(data)

    def build_soft_delete_stmt(self) -> Update | None:
        if not self.has_deleted_at_column():
            return None
        return self.build_update_stmt(
            updates={self.deleted_at_column_name(): utc_now_naive()}
        )

    def build_restore_stmt(self) -> Update | None:
        """[Soft Delete] 构造恢复已删除对象的 UPDATE 语句。"""
        if not self.has_deleted_at_column():
            return None
        return self.build_update_stmt(updates={self.deleted_at_column_name(): None})

    # ==========================================================================
    # 字段补全辅助方法
    # ==========================================================================

    @staticmethod
    def get_context_defaults() -> ContextDefaults:
        return ContextDefaults(now=utc_now_naive(), user_id=context.get_user_id())

    def fill_ins_insert_fields(self):
        """[Instance Insert] 补全实例插入所需的字段"""
        defaults = self.get_context_defaults()

        if not self.id:
            self.id = snowflake_id_generator.generate()

        if not self.created_at:
            self.created_at = defaults.now
        if not self.updated_at:
            self.updated_at = defaults.now

        if self.has_creator_id_column() and not self.creator_id and defaults.user_id:
            self.creator_id = defaults.user_id

        if self.has_updater_id_column() and not self.updater_id:
            self.updater_id = None

    def fill_ins_update_fields(self, data: dict[str, Any]) -> None:
        """[Instance Update] 补全实例更新所需的字段"""
        defaults = self.get_context_defaults()

        if self.has_updated_at_column():
            setattr(self, self.updated_at_column_name(), defaults.now)
            data[self.updated_at_column_name()] = defaults.now

        if self.has_updater_id_column():
            setattr(self, self.updater_id_column_name(), defaults.user_id)
            data[self.updater_id_column_name()] = defaults.user_id

    @classmethod
    def fill_dict_insert_fields(
        cls, raw_data: dict[str, Any], defaults: ContextDefaults
    ) -> dict[str, Any]:
        """[Dict Insert] 补全字典插入所需的字段"""
        data = raw_data.copy()

        data.setdefault("created_at", defaults.now)
        data.setdefault("updated_at", defaults.now)

        if "id" not in data:
            data["id"] = snowflake_id_generator.generate()

        if (
            cls.has_creator_id_column()
            and "creator_id" not in data
            and defaults.user_id
        ):
            data["creator_id"] = defaults.user_id

        if cls.has_updater_id_column() and "updater_id" not in data:
            data["updater_id"] = None

        valid_cols = set(cls.get_column_names())
        return {k: v for k, v in data.items() if k in valid_cols}

    def extract_db_values(self) -> dict[str, Any]:
        """[Instance -> Dict]"""
        values = {}
        valid_cols = set(self.get_column_names())
        for col_name in valid_cols:
            if hasattr(self, col_name):
                values[col_name] = getattr(self, col_name)
        return values

    @staticmethod
    async def execute_stmt(
        stmt: Executable, session_provider: SessionProvider, error_context: str
    ) -> None:
        """
        统一执行 SQL 语句。
        """
        try:
            async with session_provider() as sess, sess.begin():
                await sess.execute(stmt)
        except Exception as e:
            raise RuntimeError(f"{error_context} failed: {e}") from e

    def to_dict(self, *, exclude_column: list[str] | None = None) -> dict[str, Any]:
        return {
            col: getattr(self, col)
            for col in self.get_column_names()
            if not exclude_column or col not in exclude_column
        }

    # ==========================================================================
    # 反射与元数据工具
    # ==========================================================================

    @staticmethod
    def updater_id_column_name() -> str:
        return "updater_id"

    @staticmethod
    def creator_id_column_name() -> str:
        return "creator_id"

    @staticmethod
    def updated_at_column_name() -> str:
        return "updated_at"

    @staticmethod
    def deleted_at_column_name() -> str:
        return "deleted_at"

    @classmethod
    def has_deleted_at_column(cls) -> bool:
        return cls.has_column(cls.deleted_at_column_name())

    @classmethod
    def has_updated_at_column(cls) -> bool:
        return cls.has_column(cls.updated_at_column_name())

    @classmethod
    def has_creator_id_column(cls) -> bool:
        return cls.has_column(cls.creator_id_column_name())

    @classmethod
    def has_updater_id_column(cls) -> bool:
        return cls.has_column(cls.updater_id_column_name())

    @classmethod
    def has_column(cls, column_name: str) -> bool:
        return column_name in inspect(cls).columns

    @classmethod
    def get_column_names(cls) -> list[str]:
        return list(inspect(cls).columns.keys())

    @classmethod
    def get_column_or_none(cls, column_name: str) -> InstrumentedAttribute | None:
        return getattr(cls, column_name, None)

    @classmethod
    def get_creator_id_column(cls) -> InstrumentedAttribute | None:
        return cls.get_column_or_none(cls.creator_id_column_name())
