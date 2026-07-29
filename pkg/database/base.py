from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self

from sqlalchemy import (
    BigInteger,
    DateTime,
    String,
    Update,
    func,
    inspect,
    select,
    update,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute, Mapped, mapped_column

from pkg import request_context as context
from pkg.database.types import ColumnKey
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


class AuditActorType(StrEnum):
    """持久化审计主体类型。"""

    USER = "user"
    SYSTEM = "system"
    SERVICE = "service"
    TASK = "task"


@dataclass(frozen=True, slots=True)
class AuditActor:
    """一次写操作的审计主体。"""

    actor_type: AuditActorType
    actor_id: int | None = None

    def __post_init__(self) -> None:
        if self.actor_type is AuditActorType.USER and self.actor_id is None:
            raise ValueError("User audit actor requires actor_id")

    @classmethod
    def user(cls, user_id: int) -> Self:
        return cls(actor_type=AuditActorType.USER, actor_id=user_id)

    @classmethod
    def system(cls) -> Self:
        return cls(actor_type=AuditActorType.SYSTEM)

    @classmethod
    def service(cls, service_id: int | None = None) -> Self:
        return cls(actor_type=AuditActorType.SERVICE, actor_id=service_id)

    @classmethod
    def task(cls, task_id: int | None = None) -> Self:
        return cls(actor_type=AuditActorType.TASK, actor_id=task_id)

    def creator_values(self) -> dict[str, Any]:
        return {
            "creator_id": self.actor_id,
            "creator_type": self.actor_type.value,
        }

    def updater_values(self) -> dict[str, Any]:
        return {
            "updater_id": self.actor_id,
            "updater_type": self.actor_type.value,
        }


@dataclass(frozen=True, slots=True)
class ContextDefaults:
    now: datetime
    audit_actor: AuditActor


@dataclass(frozen=True, slots=True)
class PreparedModelUpdate:
    statement: Update
    values: Mapping[str, Any]


class ModelMixin(Base):
    """
    通用模型 Mixin
    """

    __abstract__ = True

    # --- 字段定义 ---
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=False,
    )
    creator_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    creator_type: Mapped[str] = mapped_column(String(32), nullable=False)
    updater_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, default=None
    )
    updater_type: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), default=None
    )

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

    @staticmethod
    async def get_database_now(sess: AsyncSession) -> datetime:
        """读取数据库当前 UTC 时间并返回 naive datetime。"""
        value = (await sess.execute(select(func.now()))).scalar_one()
        if not isinstance(value, datetime):
            raise RuntimeError("database current time is not a datetime")
        if value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value

    # ==========================================================================
    # 工厂方法
    # ==========================================================================

    @classmethod
    def create(
        cls,
        *,
        audit_actor: AuditActor | None = None,
        **kwargs: Any,
    ) -> Self:
        """
        创建一个新的、填充好默认值的实例（Transient 状态）。
        """
        valid_cols = set(cls.get_column_names())
        unknown_columns = sorted(set(kwargs) - valid_cols)
        if unknown_columns:
            names = ", ".join(unknown_columns)
            raise ValueError(f"Unknown {cls.__name__} create column(s): {names}")

        managed_columns = cls.audit_column_names() & set(kwargs)
        if managed_columns:
            names = ", ".join(sorted(managed_columns))
            raise ValueError(
                f"Audit column(s) {names} are managed; pass audit_actor instead"
            )

        ins = cls(**kwargs)
        ins.fill_ins_insert_fields(audit_actor=audit_actor)
        return ins

    # ==========================================================================
    # 实例写入数据准备（不执行 SQL、不提前同步更新值）
    # ==========================================================================

    def prepare_insert_values(
        self,
        *,
        audit_actor: AuditActor | None = None,
    ) -> dict[str, Any]:
        """补全实例插入字段并返回 INSERT values。"""
        state = inspect(self)
        if not state.transient:
            raise RuntimeError(
                f"prepare_insert_values() is strictly for INSERT operations. "
                f"Object {self.__class__.__name__}(id={self.id}) is already persistent/detached. "
                f"Please prepare an update instead."
            )

        self.fill_ins_insert_fields(audit_actor=audit_actor)
        return self.extract_db_values()

    @staticmethod
    def normalize_update_column_name(column: ColumnKey) -> str:
        if isinstance(column, InstrumentedAttribute):
            return column.key
        return column

    def prepare_update(
        self,
        updates: Mapping[ColumnKey, Any] | None = None,
        *,
        audit_actor: AuditActor | None = None,
        **kwargs: Any,
    ) -> PreparedModelUpdate:
        """准备实例 UPDATE；提交前不修改内存实例。"""
        state = inspect(self)
        if state.transient:
            raise RuntimeError(
                f"prepare_update() requires a persisted {self.__class__.__name__} instance"
            )
        if self.id is None:
            raise RuntimeError("Instance update requires a primary key")

        data: dict[str, Any] = {}
        raw_updates: dict[ColumnKey, Any] = dict(updates or {})
        for kwarg_name, kwarg_value in kwargs.items():
            raw_updates[kwarg_name] = kwarg_value

        normalized_updates: list[tuple[str, Any]] = []
        invalid_columns: list[str] = []
        protected_columns = self.audit_column_names() | {
            "id",
            "created_at",
            self.updated_at_column_name(),
        }
        for key, value in raw_updates.items():
            column_name = self.normalize_update_column_name(key)
            if not self.has_column(column_name) or column_name in protected_columns:
                invalid_columns.append(column_name)
                continue
            normalized_updates.append((column_name, value))

        if invalid_columns:
            names = ", ".join(sorted(invalid_columns))
            raise ValueError(
                f"Unknown or managed {self.__class__.__name__} update column(s): {names}"
            )

        for column_name, value in normalized_updates:
            if isinstance(value, datetime) and value.tzinfo is not None:
                value = value.replace(tzinfo=None)
            data[column_name] = value

        defaults = self.get_context_defaults(audit_actor=audit_actor)
        if self.has_updated_at_column():
            deleted_column = self.deleted_at_column_name()
            deleted_at = data.get(deleted_column)
            data[self.updated_at_column_name()] = deleted_at or defaults.now
        if self.has_updater_id_column():
            data.update(defaults.audit_actor.updater_values())

        statement = (
            update(self.__class__).where(self.__class__.id == self.id).values(data)
        )
        return PreparedModelUpdate(statement=statement, values=data)

    def prepare_soft_delete(
        self,
        *,
        audit_actor: AuditActor | None = None,
    ) -> PreparedModelUpdate | None:
        if not self.has_deleted_at_column():
            return None
        return self.prepare_update(
            updates={self.deleted_at_column_name(): utc_now_naive()},
            audit_actor=audit_actor,
        )

    def prepare_restore(
        self,
        *,
        audit_actor: AuditActor | None = None,
    ) -> PreparedModelUpdate | None:
        if not self.has_deleted_at_column():
            return None
        return self.prepare_update(
            updates={self.deleted_at_column_name(): None},
            audit_actor=audit_actor,
        )

    def apply_persisted_values(self, values: Mapping[str, Any]) -> None:
        """仅在事务提交成功后同步内存实例。"""
        for column_name, value in values.items():
            setattr(self, column_name, value)

    # ==========================================================================
    # 字段补全辅助方法
    # ==========================================================================

    @staticmethod
    def resolve_audit_actor(audit_actor: AuditActor | None = None) -> AuditActor:
        """优先使用显式 actor；无请求登录态时使用 system actor。"""
        if audit_actor is not None:
            return audit_actor
        try:
            return AuditActor.user(context.get_user_id())
        except LookupError:
            return AuditActor.system()

    @classmethod
    def get_context_defaults(
        cls,
        *,
        audit_actor: AuditActor | None = None,
    ) -> ContextDefaults:
        return ContextDefaults(
            now=utc_now_naive(),
            audit_actor=cls.resolve_audit_actor(audit_actor),
        )

    def fill_ins_insert_fields(
        self,
        *,
        audit_actor: AuditActor | None = None,
    ) -> None:
        """[Instance Insert] 补全实例插入所需的字段"""
        defaults = self.get_context_defaults(audit_actor=audit_actor)

        if not self.id:
            self.id = snowflake_id_generator.generate()

        if not self.created_at:
            self.created_at = defaults.now
        if not self.updated_at:
            self.updated_at = defaults.now

        if self.has_creator_id_column() and (
            audit_actor is not None or not getattr(self, "creator_type", None)
        ):
            for column_name, value in defaults.audit_actor.creator_values().items():
                setattr(self, column_name, value)

        if self.has_updater_id_column():
            self.updater_id = None
            self.updater_type = None

    @classmethod
    def fill_dict_insert_fields(
        cls, raw_data: dict[str, Any], defaults: ContextDefaults
    ) -> dict[str, Any]:
        """[Dict Insert] 补全字典插入所需的字段"""
        data = raw_data.copy()
        valid_cols = set(cls.get_column_names())
        unknown_columns = sorted(set(data) - valid_cols)
        if unknown_columns:
            names = ", ".join(unknown_columns)
            raise ValueError(f"Unknown {cls.__name__} insert column(s): {names}")

        managed_columns = cls.audit_column_names() & set(data)
        if managed_columns:
            names = ", ".join(sorted(managed_columns))
            raise ValueError(f"Managed audit column(s) in insert row: {names}")

        data.setdefault("created_at", defaults.now)
        data.setdefault("updated_at", defaults.now)

        if "id" not in data:
            data["id"] = snowflake_id_generator.generate()

        if cls.has_creator_id_column():
            data.update(defaults.audit_actor.creator_values())

        if cls.has_updater_id_column():
            data["updater_id"] = None
            data["updater_type"] = None

        return data

    def extract_db_values(self) -> dict[str, Any]:
        """[Instance -> Dict]"""
        values = {}
        valid_cols = set(self.get_column_names())
        for col_name in valid_cols:
            if hasattr(self, col_name):
                values[col_name] = getattr(self, col_name)
        return values

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
    def updater_type_column_name() -> str:
        return "updater_type"

    @staticmethod
    def creator_id_column_name() -> str:
        return "creator_id"

    @staticmethod
    def creator_type_column_name() -> str:
        return "creator_type"

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
    def audit_column_names(cls) -> set[str]:
        return {
            cls.creator_id_column_name(),
            cls.creator_type_column_name(),
            cls.updater_id_column_name(),
            cls.updater_type_column_name(),
        }

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
