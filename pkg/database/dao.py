from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, cast

from sqlalchemy import (
    ColumnElement,
    Insert,
    Select,
    Update,
    distinct,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, make_transient_to_detached

from pkg.database.base import AuditActor, ModelMixin, SessionProvider
from pkg.database.types import ColumnKey


class BaseDao[T: ModelMixin]:
    """提供策略感知的 statement factory、session provider 与通用持久化操作。"""

    _model_cls: type[T]

    # ==========================================================================
    # 初始化与模型约束
    # ==========================================================================

    def __init__(
        self,
        *,
        session_provider: SessionProvider,
        read_session_provider: SessionProvider | None = None,
    ) -> None:
        self._session_provider = session_provider
        self._read_session_provider = read_session_provider or session_provider
        _ = self.model_cls

    @property
    def model_cls(self) -> type[T]:
        model_cls: type[T] | None = getattr(type(self), "_model_cls", None)
        if model_cls is None:
            raise ValueError(f"DAO {self.__class__.__name__} must define _model_cls")
        return model_cls

    @property
    def session_provider(self) -> SessionProvider:
        return self._session_provider

    @property
    def read_session_provider(self) -> SessionProvider:
        return self._read_session_provider

    def _assert_instance_model_match(self, instance: ModelMixin) -> None:
        if instance.__class__ is not self.model_cls:
            raise TypeError(
                f"{self.__class__.__name__} expects {self.model_cls.__name__}, "
                f"got {instance.__class__.__name__}"
            )

    def _assert_instances_model_match(self, items: list[T]) -> None:
        for item in items:
            self._assert_instance_model_match(item)

    # ==========================================================================
    # 实例构造
    # ==========================================================================

    def create(self, *, audit_actor: AuditActor | None = None, **kwargs: Any) -> T:
        return self.model_cls.create(audit_actor=audit_actor, **kwargs)

    # ==========================================================================
    # Statement 构造
    # ==========================================================================

    def _policy_conditions(
        self,
        *,
        include_deleted: bool,
    ) -> tuple[ColumnElement[bool], ...]:
        if include_deleted or not self.model_cls.has_deleted_at_column():
            return ()
        deleted_column = self.model_cls.get_column_or_none(
            self.model_cls.deleted_at_column_name()
        )
        if deleted_column is None:
            raise RuntimeError(
                f"Unable to resolve {self.model_cls.__name__} deleted column"
            )
        return (cast(ColumnElement[bool], deleted_column.is_(None)),)

    def select_stmt(
        self,
        *columns: InstrumentedAttribute[Any],
        include_deleted: bool = False,
    ) -> Select[Any]:
        """构造应用软删除策略的 SELECT，排序和业务条件由调用方显式追加。"""
        statement = (
            select(*columns).select_from(self.model_cls)
            if columns
            else select(self.model_cls)
        )
        return statement.where(
            *self._policy_conditions(include_deleted=include_deleted)
        )

    def count_stmt(
        self,
        column: InstrumentedAttribute[Any] | None = None,
        *,
        is_distinct: bool = False,
        include_deleted: bool = False,
    ) -> Select[Any]:
        """构造与 SELECT 使用相同软删除策略的 COUNT。"""
        target: Any = column if column is not None else self.model_cls.id
        if is_distinct:
            target = distinct(target)
        return (
            select(func.count(target))
            .select_from(self.model_cls)
            .where(*self._policy_conditions(include_deleted=include_deleted))
        )

    def update_stmt(
        self,
        *conditions: ColumnElement[bool],
        values: Mapping[ColumnKey, Any],
        audit_actor: AuditActor | None = None,
        include_deleted: bool = False,
    ) -> Update:
        """构造受字段、审计和范围保护的批量 UPDATE。"""
        if not conditions:
            raise ValueError(
                f"{self.model_cls.__name__} update requires explicit conditions"
            )
        if not values:
            raise ValueError("UPDATE requires at least one value")

        protected_columns = self.model_cls.audit_column_names() | {
            "id",
            "created_at",
            self.model_cls.updated_at_column_name(),
        }
        invalid_columns: list[str] = []
        normalized_values: dict[str, Any] = {}
        for key, value in values.items():
            column_name = self.model_cls.normalize_update_column_name(key)
            if (
                not self.model_cls.has_column(column_name)
                or column_name in protected_columns
            ):
                invalid_columns.append(column_name)
                continue
            if isinstance(value, datetime) and value.tzinfo is not None:
                value = value.replace(tzinfo=None)
            normalized_values[column_name] = value

        if invalid_columns:
            names = ", ".join(sorted(invalid_columns))
            raise ValueError(
                f"Unknown or managed {self.model_cls.__name__} update column(s): {names}"
            )

        defaults = self.model_cls.get_context_defaults(audit_actor=audit_actor)
        if self.model_cls.has_updated_at_column():
            deleted_at = normalized_values.get(self.model_cls.deleted_at_column_name())
            normalized_values[self.model_cls.updated_at_column_name()] = (
                deleted_at or defaults.now
            )
        if self.model_cls.has_updater_id_column():
            normalized_values.update(defaults.audit_actor.updater_values())

        return (
            update(self.model_cls)
            .where(
                *self._policy_conditions(include_deleted=include_deleted),
                *conditions,
            )
            .values(normalized_values)
            .execution_options(synchronize_session=False)
        )

    # ==========================================================================
    # 查询执行
    # ==========================================================================

    async def fetch_first(
        self,
        statement: Select[Any],
        *,
        session: AsyncSession | None = None,
    ) -> T | None:
        """使用 LIMIT 1 查询首个 ORM 实例；需要确定性时由调用方排序。"""
        statement = statement.limit(1)
        if session is not None:
            result = await session.execute(statement)
            return cast(T | None, result.scalars().first())

        async with self._read_session_provider() as owned_session:
            result = await owned_session.execute(statement)
            return cast(T | None, result.scalars().first())

    async def fetch_one(
        self,
        statement: Select[Any],
        *,
        session: AsyncSession | None = None,
    ) -> T | None:
        """查询零个或一个 ORM 实例；传入 session 时不管理其生命周期。"""
        if session is not None:
            result = await session.execute(statement)
            return cast(T | None, result.scalars().one_or_none())

        async with self._read_session_provider() as owned_session:
            result = await owned_session.execute(statement)
            return cast(T | None, result.scalars().one_or_none())

    async def fetch_all(
        self,
        statement: Select[Any],
        *,
        session: AsyncSession | None = None,
    ) -> list[T]:
        """查询 ORM 实例列表；传入 session 时不管理其生命周期。"""
        if session is not None:
            result = await session.execute(statement)
            return cast(list[T], result.scalars().all())

        async with self._read_session_provider() as owned_session:
            result = await owned_session.execute(statement)
            return cast(list[T], result.scalars().all())

    async def query_by_primary_id(
        self,
        primary_id: int,
        *,
        creator_id: int | None = None,
        include_deleted: bool = False,
    ) -> T | None:
        statement = self.select_stmt(include_deleted=include_deleted).where(
            self.model_cls.id == primary_id
        )
        if creator_id is not None:
            creator_column = self.model_cls.get_creator_id_column()
            if creator_column is None:
                raise RuntimeError(
                    f"Unable to resolve {self.model_cls.__name__} creator column"
                )
            statement = statement.where(creator_column == creator_id)

        return await self.fetch_one(statement)

    async def query_by_ids(self, ids: list[int]) -> list[T]:
        if not ids:
            return []
        statement = self.select_stmt().where(self.model_cls.id.in_(ids))
        return await self.fetch_all(statement)

    # ==========================================================================
    # 事务管理
    # ==========================================================================

    @asynccontextmanager
    async def transaction(
        self,
        *,
        autoflush: bool = True,
    ) -> AsyncGenerator[AsyncSession, None]:
        """开启显式主库事务并直接暴露 AsyncSession。"""
        async with (
            self._session_provider(autoflush=autoflush) as session,
            session.begin(),
        ):
            yield session

    # ==========================================================================
    # 单实例写入
    # ==========================================================================

    async def insert(
        self,
        instance: T,
        *,
        audit_actor: AuditActor | None = None,
    ) -> None:
        self._assert_instance_model_match(instance)
        values = instance.prepare_insert_values(audit_actor=audit_actor)
        async with self._session_provider() as session:
            await session.execute(insert(self.model_cls).values(values))
            await session.commit()
        make_transient_to_detached(instance)

    async def update(
        self,
        instance: T,
        updates: Mapping[ColumnKey, Any] | None = None,
        *,
        audit_actor: AuditActor | None = None,
        **kwargs: Any,
    ) -> T:
        self._assert_instance_model_match(instance)
        prepared = instance.prepare_update(
            updates=updates,
            audit_actor=audit_actor,
            **kwargs,
        )
        async with self._session_provider() as session:
            result = await session.execute(prepared.statement)
            rowcount = getattr(result, "rowcount", None)
            await session.commit()
        if rowcount == 0:
            raise RuntimeError(
                f"{self.model_cls.__name__}(id={instance.id}) no longer exists"
            )
        instance.apply_persisted_values(prepared.values)
        return instance

    async def soft_delete(
        self,
        instance: T,
        *,
        audit_actor: AuditActor | None = None,
    ) -> None:
        self._assert_instance_model_match(instance)
        prepared = instance.prepare_soft_delete(audit_actor=audit_actor)
        if prepared is None:
            raise ValueError(
                f"{self.model_cls.__name__} does not support soft deletion"
            )
        async with self._session_provider() as session:
            result = await session.execute(prepared.statement)
            rowcount = getattr(result, "rowcount", None)
            await session.commit()
        if rowcount == 0:
            raise RuntimeError(
                f"{self.model_cls.__name__}(id={instance.id}) no longer exists"
            )
        instance.apply_persisted_values(prepared.values)

    async def restore(
        self,
        instance: T,
        *,
        audit_actor: AuditActor | None = None,
    ) -> None:
        self._assert_instance_model_match(instance)
        prepared = instance.prepare_restore(audit_actor=audit_actor)
        if prepared is None:
            raise ValueError(f"{self.model_cls.__name__} does not support restoration")
        async with self._session_provider() as session:
            result = await session.execute(prepared.statement)
            rowcount = getattr(result, "rowcount", None)
            await session.commit()
        if rowcount == 0:
            raise RuntimeError(
                f"{self.model_cls.__name__}(id={instance.id}) no longer exists"
            )
        instance.apply_persisted_values(prepared.values)

    # ==========================================================================
    # 批量写入
    # ==========================================================================

    async def execute_update(
        self,
        statement: Update,
        *,
        session: AsyncSession | None = None,
    ) -> int:
        """执行 update_stmt() 生成的 UPDATE；传入 session 时不管理事务。"""
        if session is not None:
            result = await session.execute(statement)
            return getattr(result, "rowcount", 0) or 0

        async with self._session_provider() as owned_session:
            result = await owned_session.execute(statement)
            rowcount = getattr(result, "rowcount", 0) or 0
            await owned_session.commit()
        return rowcount

    def build_insert_rows_stmt(
        self,
        *,
        rows: list[dict[str, Any]],
        audit_actor: AuditActor | None = None,
    ) -> Insert | None:
        if not rows:
            return None
        defaults = self.model_cls.get_context_defaults(audit_actor=audit_actor)
        values = [self.model_cls.fill_dict_insert_fields(row, defaults) for row in rows]
        return insert(self.model_cls).values(values)

    async def insert_rows(
        self,
        *,
        rows: list[dict[str, Any]],
        audit_actor: AuditActor | None = None,
    ) -> None:
        statement = self.build_insert_rows_stmt(rows=rows, audit_actor=audit_actor)
        if statement is not None:
            async with self._session_provider() as session:
                await session.execute(statement)
                await session.commit()

    def build_insert_instances_stmt(
        self,
        *,
        items: list[T],
        audit_actor: AuditActor | None = None,
    ) -> Insert | None:
        if not items:
            return None
        self._assert_instances_model_match(items)
        values = [item.prepare_insert_values(audit_actor=audit_actor) for item in items]
        return insert(self.model_cls).values(values)

    async def insert_instances(
        self,
        *,
        items: list[T],
        audit_actor: AuditActor | None = None,
    ) -> None:
        statement = self.build_insert_instances_stmt(
            items=items,
            audit_actor=audit_actor,
        )
        if statement is None:
            return
        async with self._session_provider() as session:
            await session.execute(statement)
            await session.commit()
        for item in items:
            make_transient_to_detached(cast(ModelMixin, item))
