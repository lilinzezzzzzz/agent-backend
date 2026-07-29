from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass
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

from pkg.database.audit import AuditActor
from pkg.database.base import ModelMixin
from pkg.database.session import SessionProvider
from pkg.database.types import ColumnKey
from pkg.toolkit.timer import utc_now_naive


@dataclass(frozen=True, slots=True)
class PageResult[T]:
    """数据库分页查询结果。"""

    items: list[T]
    page: int
    limit: int
    total: int


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
        return (cast(ColumnElement[bool], self.model_cls.deleted_at.is_(None)),)

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
        normalized_values = self.model_cls.prepare_update_values(
            values.items(),
            audit_actor=audit_actor,
        )

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

    async def fetch_page(
        self,
        statement: Select[Any],
        *,
        page: int,
        limit: int,
        count_statement: Select[Any] | None = None,
        session: AsyncSession | None = None,
    ) -> PageResult[T]:
        """分页查询 ORM 实例；需要确定性时由调用方提供唯一排序。"""
        if page < 1:
            raise ValueError("page must be greater than or equal to 1")
        if limit < 1:
            raise ValueError("limit must be greater than or equal to 1")

        unpaged_statement = statement.order_by(None).limit(None).offset(None)
        counter = (
            count_statement
            if count_statement is not None
            else select(func.count()).select_from(unpaged_statement.subquery())
        )
        paged_statement = statement.offset((page - 1) * limit).limit(limit)

        session_context = (
            nullcontext(session)
            if session is not None
            else self._read_session_provider()
        )
        async with session_context as active_session:
            total = (await active_session.execute(counter)).scalar_one()
            result = await active_session.execute(paged_statement)
            return PageResult(
                items=cast(list[T], result.scalars().all()),
                page=page,
                limit=limit,
                total=cast(int, total),
            )

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
            statement = statement.where(self.model_cls.creator_id == creator_id)

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
        values = instance.prepare_instance_update_values(
            updates=updates,
            audit_actor=audit_actor,
            **kwargs,
        )
        statement = (
            update(self.model_cls)
            .where(self.model_cls.id == instance.id)
            .values(values)
            .execution_options(synchronize_session=False)
        )
        async with self._session_provider() as session, session.begin():
            result = await session.execute(statement)
            if getattr(result, "rowcount", None) == 0:
                raise RuntimeError(
                    f"{self.model_cls.__name__}(id={instance.id}) no longer exists"
                )
        instance.apply_persisted_values(values)
        return instance

    async def soft_delete(
        self,
        instance: T,
        *,
        audit_actor: AuditActor | None = None,
    ) -> None:
        self._assert_instance_model_match(instance)
        if not self.model_cls.has_deleted_at_column():
            raise ValueError(
                f"{self.model_cls.__name__} does not support soft deletion"
            )
        await self.update(
            instance,
            updates={"deleted_at": utc_now_naive()},
            audit_actor=audit_actor,
        )

    async def restore(
        self,
        instance: T,
        *,
        audit_actor: AuditActor | None = None,
    ) -> None:
        self._assert_instance_model_match(instance)
        if not self.model_cls.has_deleted_at_column():
            raise ValueError(f"{self.model_cls.__name__} does not support restoration")
        await self.update(
            instance,
            updates={"deleted_at": None},
            audit_actor=audit_actor,
        )

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
        defaults = self.model_cls.get_write_defaults(audit_actor=audit_actor)
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
