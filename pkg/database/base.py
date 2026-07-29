from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Self

from sqlalchemy import (
    BigInteger,
    DateTime,
    String,
    inspect,
)
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute, Mapped, mapped_column
from sqlalchemy.orm.attributes import set_committed_value

from pkg import request_context as context
from pkg.database.audit import AuditActor
from pkg.database.types import ColumnKey
from pkg.ids import snowflake_id_generator
from pkg.toolkit.timer import utc_now_naive

_AUDIT_COLUMNS = frozenset({"creator_id", "creator_type", "updater_id", "updater_type"})
_MANAGED_UPDATE_COLUMNS = _AUDIT_COLUMNS | {"id", "created_at", "updated_at"}


class Base(DeclarativeBase):
    """SQLAlchemy 2.x 声明式基类。"""


@dataclass(frozen=True, slots=True)
class WriteDefaults:
    """同一批写入共享的时间与审计主体。"""

    now: datetime
    audit_actor: AuditActor


class ModelMixin(Base):
    """提供主键、审计字段、软删除字段与受保护的写入准备逻辑。"""

    __abstract__ = True

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

    @classmethod
    def create(
        cls,
        *,
        audit_actor: AuditActor | None = None,
        **kwargs: Any,
    ) -> Self:
        """创建已补齐主键、时间和创建审计字段的 transient 实例。"""
        cls._validate_insert_columns(kwargs, operation="create")
        instance = cls(**kwargs)
        instance._fill_insert_fields(audit_actor=audit_actor)
        return instance

    def prepare_insert_values(
        self,
        *,
        audit_actor: AuditActor | None = None,
    ) -> dict[str, Any]:
        """补齐 transient 实例的插入字段并返回数据库列值。"""
        if not inspect(self).transient:
            raise RuntimeError(
                f"prepare_insert_values() is strictly for INSERT operations. "
                f"Object {self.__class__.__name__}(id={self.id}) is already persistent/detached. "
                "Please prepare an update instead."
            )

        self._fill_insert_fields(audit_actor=audit_actor)
        return self.extract_db_values()

    def prepare_instance_update_values(
        self,
        updates: Mapping[ColumnKey, Any] | None = None,
        *,
        audit_actor: AuditActor | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """校验实例状态和显式字段，并返回规范化的 UPDATE values。"""
        state = inspect(self)
        if state.transient:
            raise RuntimeError(
                f"prepare_instance_update_values() requires a persisted {self.__class__.__name__} instance"
            )
        if self.id is None:
            raise RuntimeError("Instance update requires a primary key")

        update_items = [*(updates or {}).items(), *kwargs.items()]
        declared_columns = {
            self.normalize_column_name(key) for key, _value in update_items
        }
        dirty_columns = {
            attr.key
            for attr in state.attrs
            if self.has_column(attr.key) and attr.history.has_changes()
        }
        undeclared_columns = dirty_columns.difference(declared_columns)
        if undeclared_columns:
            names = ", ".join(sorted(undeclared_columns))
            raise ValueError(
                f"Modified {self.__class__.__name__} column(s) must be passed explicitly: {names}"
            )

        return self.prepare_update_values(
            update_items,
            audit_actor=audit_actor,
        )

    def apply_persisted_values(self, values: Mapping[str, Any]) -> None:
        """事务提交成功后，将实际写入值同步到内存实例。"""
        for column_name, value in values.items():
            set_committed_value(self, column_name, value)

    @classmethod
    def prepare_update_values(
        cls,
        updates: Iterable[tuple[ColumnKey, Any]],
        *,
        audit_actor: AuditActor | None = None,
    ) -> dict[str, Any]:
        """校验业务更新字段，并统一补齐更新时间与更新审计字段。"""
        values: dict[str, Any] = {}
        invalid_columns: set[str] = set()
        duplicate_columns: set[str] = set()

        for key, value in updates:
            column_name = cls.normalize_column_name(key)
            if column_name in values:
                duplicate_columns.add(column_name)
                continue
            if (
                not cls.has_column(column_name)
                or column_name in _MANAGED_UPDATE_COLUMNS
            ):
                invalid_columns.add(column_name)
                continue
            values[column_name] = (
                cls.normalize_datetime(value) if isinstance(value, datetime) else value
            )

        if duplicate_columns:
            names = ", ".join(sorted(duplicate_columns))
            raise ValueError(f"Duplicate {cls.__name__} update column(s): {names}")
        if invalid_columns:
            names = ", ".join(sorted(invalid_columns))
            raise ValueError(
                f"Unknown or managed {cls.__name__} update column(s): {names}"
            )
        if not values:
            raise ValueError("UPDATE requires at least one value")

        defaults = cls.get_write_defaults(audit_actor=audit_actor)
        deleted_at = values.get("deleted_at")
        values["updated_at"] = deleted_at if deleted_at is not None else defaults.now
        values.update(defaults.audit_actor.updater_values())
        return values

    @staticmethod
    def resolve_audit_actor(audit_actor: AuditActor | None = None) -> AuditActor:
        """优先使用显式 actor；无请求用户上下文时使用 system actor。"""
        if audit_actor is not None:
            return audit_actor
        try:
            return AuditActor.user(context.get_user_id())
        except LookupError:
            return AuditActor.system()

    @classmethod
    def get_write_defaults(
        cls,
        *,
        audit_actor: AuditActor | None = None,
    ) -> WriteDefaults:
        return WriteDefaults(
            now=utc_now_naive(),
            audit_actor=cls.resolve_audit_actor(audit_actor),
        )

    def _fill_insert_fields(
        self,
        *,
        audit_actor: AuditActor | None = None,
    ) -> None:
        """补齐实例缺失的插入字段，并清空不适用于创建操作的更新审计。"""
        defaults = self.get_write_defaults(audit_actor=audit_actor)

        if self.id is None:
            self.id = snowflake_id_generator.generate()
        if self.created_at is None:
            self.created_at = defaults.now
        if self.updated_at is None:
            self.updated_at = defaults.now

        for column_name in self.get_column_names():
            value = getattr(self, column_name, None)
            if isinstance(value, datetime):
                setattr(self, column_name, self.normalize_datetime(value))

        if audit_actor is not None or self.creator_type is None:
            for column_name, value in defaults.audit_actor.creator_values().items():
                setattr(self, column_name, value)

        self.updater_id = None
        self.updater_type = None

    @classmethod
    def fill_dict_insert_fields(
        cls,
        raw_data: Mapping[str, Any],
        defaults: WriteDefaults,
    ) -> dict[str, Any]:
        """复制并补齐一行批量 INSERT 数据，不修改调用方传入的 mapping。"""
        cls._validate_insert_columns(raw_data, operation="insert")
        values = {
            column_name: cls.normalize_datetime(value)
            if isinstance(value, datetime)
            else value
            for column_name, value in raw_data.items()
        }
        if values.get("created_at") is None:
            values["created_at"] = defaults.now
        if values.get("updated_at") is None:
            values["updated_at"] = defaults.now
        if values.get("id") is None:
            values["id"] = snowflake_id_generator.generate()
        values.update(defaults.audit_actor.creator_values())
        values["updater_id"] = None
        values["updater_type"] = None
        return values

    @classmethod
    def _validate_insert_columns(
        cls,
        values: Mapping[str, Any],
        *,
        operation: str,
    ) -> None:
        unknown_columns = set(values) - set(cls.get_column_names())
        if unknown_columns:
            names = ", ".join(sorted(unknown_columns))
            raise ValueError(f"Unknown {cls.__name__} {operation} column(s): {names}")

        managed_columns = _AUDIT_COLUMNS & set(values)
        if managed_columns:
            names = ", ".join(sorted(managed_columns))
            raise ValueError(f"Managed audit column(s) in {operation} values: {names}")

    def extract_db_values(self) -> dict[str, Any]:
        """提取当前实例中已赋值的数据库列。"""
        return {
            column_name: getattr(self, column_name)
            for column_name in self.get_column_names()
            if hasattr(self, column_name)
        }

    def to_dict(
        self,
        *,
        exclude_column: Collection[str] | None = None,
    ) -> dict[str, Any]:
        return {
            column_name: getattr(self, column_name)
            for column_name in self.get_column_names()
            if exclude_column is None or column_name not in exclude_column
        }

    @staticmethod
    def normalize_column_name(column: ColumnKey) -> str:
        return column.key if isinstance(column, InstrumentedAttribute) else column

    @staticmethod
    def normalize_datetime(value: datetime) -> datetime:
        """数据库时间字段统一使用 naive UTC；naive 输入按 UTC 解释。"""
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    @classmethod
    def has_deleted_at_column(cls) -> bool:
        """返回是否启用通用软删除策略；特殊模型可覆写为 False。"""
        return cls.has_column("deleted_at")

    @classmethod
    def has_column(cls, column_name: str) -> bool:
        return column_name in inspect(cls).columns

    @classmethod
    def get_column_names(cls) -> list[str]:
        return list(inspect(cls).columns.keys())
