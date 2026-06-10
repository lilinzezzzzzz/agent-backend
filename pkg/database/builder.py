from collections.abc import Mapping
from datetime import datetime
from typing import Any, Self, cast

from sqlalchemy import ColumnElement, Delete, Select, Update, func, or_, select, update
from sqlalchemy.orm import InstrumentedAttribute, Mapped

from pkg.database.base import ModelMixin, SessionProvider
from pkg.database.types import ColumnKey
from pkg.logger import logger
from pkg import request_context as context
from pkg.toolkit.list import unique_list
from pkg.toolkit.timer import utc_now_naive

"""
构建器 (Builder)
"""


class BaseBuilder[T: ModelMixin]:
    """SQL查询构建器基类"""

    __slots__ = ("_model_cls", "_stmt", "_session_provider")

    def __init__(self, model_cls: type[T], *, session_provider: SessionProvider):
        self._model_cls: type[T] = model_cls
        self._stmt: Select | Delete | Update | None = None
        self._session_provider = session_provider

    @property
    def stmt(self) -> Select | Delete | Update:
        """获取当前语句，若未初始化则抛出异常"""
        if self._stmt is None:
            raise RuntimeError("Statement not initialized")
        return self._stmt

    # --- 条件构造 ---
    def where(self, *conditions: ColumnElement[bool]) -> Self:
        if conditions:
            self._stmt = self.stmt.where(*conditions)
        return self

    def apply_kwargs_filters(self, **kwargs):
        """将 kwargs 筛选条件应用到 builder（querier/counter/updater）

        Args:
            **kwargs: 字段名=值 的筛选条件

        Returns:
            应用了筛选条件的 builder

        Example:
            await builder.apply_kwargs_filters(dao.querier, organization_id=1, status="active").all()
            await builder.apply_kwargs_filters(dao.counter, organization_id=1).count()
        """
        for k, v in kwargs.items():
            if column := self._model_cls.get_column_or_none(k):
                self.where(column == v)
        return self

    def eq_(self, column: InstrumentedAttribute | Mapped, value: Any) -> Self:
        return self.where(column == value)

    def ne_(self, column: InstrumentedAttribute, value: Any) -> Self:
        return self.where(column != value)

    def gt_(self, column: InstrumentedAttribute, value: Any) -> Self:
        return self.where(column > value)

    def lt_(self, column: InstrumentedAttribute, value: Any) -> Self:
        return self.where(column < value)

    def ge_(self, column: InstrumentedAttribute, value: Any) -> Self:
        return self.where(column >= value)

    def le_(self, column: InstrumentedAttribute, value: Any) -> Self:
        return self.where(column <= value)

    def in_(self, column: InstrumentedAttribute | Mapped, values: list | tuple) -> Self:
        """
        修复了空列表逻辑：空列表应该返回 False 条件，而不是返回 self (忽略条件)。
        """
        if not values:
            raise ValueError(f"in_() func values cannot be empty for column {column}")

        unique = unique_list(values, exclude_none=True)

        if len(unique) == 1:
            return self.where(column == unique[0])

        return self.where(column.in_(unique))

    def like(self, column: InstrumentedAttribute, pattern: str) -> Self:
        return self.where(column.like(f"%{pattern}%"))

    def is_null(self, column: InstrumentedAttribute) -> Self:
        return self.where(column.is_(None))

    def or_(self, *conditions: ColumnElement[bool]) -> Self:
        return self.where(or_(*conditions)) if conditions else self

    def _apply_delete_at_is_none(self) -> None:
        if deleted_column := self._model_cls.get_column_or_none(
            self._model_cls.deleted_at_column_name()
        ):
            self._stmt = self.stmt.where(deleted_column.is_(None))


class QueryBuilder[T: ModelMixin](BaseBuilder[T]):
    def __init__(
        self,
        model_cls: type[T],
        *,
        session_provider: SessionProvider,
        initial_where: ColumnElement[bool] | None = None,
        custom_stmt: Select | None = None,
        include_deleted: bool | None = None,
    ):
        super().__init__(model_cls, session_provider=session_provider)

        self._stmt = custom_stmt if custom_stmt is not None else select(self._model_cls)

        if include_deleted is False and self._model_cls.has_deleted_at_column():
            self._apply_delete_at_is_none()

        if initial_where is not None:
            self._stmt = self._stmt.where(initial_where)

    @property
    def select_stmt(self) -> Select:
        if not isinstance(self._stmt, Select):
            raise RuntimeError("Statement is not a Select")
        return self._stmt

    # --- 排序与分组 ---
    def distinct_(self, *cols: InstrumentedAttribute) -> Self:
        self._stmt = self.select_stmt.distinct(*cols)
        return self

    def desc_(self, col: InstrumentedAttribute | Mapped) -> Self:
        self._stmt = self.select_stmt.order_by(col.desc())
        return self

    def asc_(self, col: InstrumentedAttribute) -> Self:
        self._stmt = self.select_stmt.order_by(col.asc())
        return self

    def paginate(self, *, page: int, limit: int) -> Self:
        if not isinstance(page, int) or page < 1:
            raise ValueError("page must be greater than or equal to 1")

        if not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be greater than or equal to 1")

        self._stmt = self.select_stmt.offset((page - 1) * limit).limit(limit)
        return self

    async def all(self) -> list[T]:
        try:
            async with self._session_provider() as sess:
                result = await sess.execute(self.select_stmt)
                await sess.commit()
                return cast(list[T], result.scalars().all())
        except Exception as e:
            raise RuntimeError(
                f"Error when querying all data, {self._model_cls.__name__}: {e}"
            ) from e

    async def first(self) -> T | None:
        try:
            async with self._session_provider() as sess:
                result = await sess.execute(self.select_stmt)
                await sess.commit()
                return result.scalars().first()
        except Exception as e:
            raise RuntimeError(
                f"Error when querying first data, {self._model_cls.__name__}: {e}"
            ) from e

    async def values(self) -> list[tuple]:
        """返回列查询的元组列表

        用于 col_querier() 场景，返回元组列表：
        - 单列查询：返回单元素元组列表
        - 多列查询：返回多元素元组列表

        Returns:
            元组列表

        Example:
            # 单列查询
            ids = await dao.col_querier(Model.id).eq_(Model.org_id, 1).values()
            # 返回：[(1,), (2,), (3,)]

            # 多列查询
            rows = await dao.col_querier(Model.id, Model.name).eq_(...).values()
            # 返回：[(1, "Alice"), (2, "Bob")]
        """
        try:
            async with self._session_provider() as sess:
                result = await sess.execute(self.stmt)
                await sess.commit()
                return cast(list[tuple], result.all())
        except Exception as e:
            raise RuntimeError(
                f"Error when querying values, {self._model_cls.__name__}: {e}"
            ) from e

    async def exists(self) -> bool:
        """Check if any record matches the current query conditions

        Returns:
            True if at least one record matches, False otherwise

        Example:
            has_active = await dao.querier.eq_(Model.status, "active").exists()
        """
        return await self.first() is not None


class CountBuilder[T: ModelMixin](BaseBuilder[T]):
    def __init__(
        self,
        model_cls: type[T],
        *,
        session_provider: SessionProvider,
        custom_stmt: Select | None = None,
        include_deleted: bool | None = None,
    ):
        super().__init__(model_cls, session_provider=session_provider)

        # 如果提供了 custom_stmt，直接使用；否则默认统计主键 id
        if custom_stmt is not None:
            self._stmt = custom_stmt
        else:
            self._stmt = select(func.count(self._model_cls.id))

        if include_deleted is not True and self._model_cls.has_deleted_at_column():
            self._apply_delete_at_is_none()

    @property
    def count_stmt(self) -> Select:
        if not isinstance(self._stmt, Select):
            raise RuntimeError("Statement is not a Select")
        return self._stmt

    async def count(self) -> int:
        try:
            async with self._session_provider() as sess:
                total = (await sess.execute(self.count_stmt)).scalar()
                await sess.commit()
                return total if total is not None else 0
        except Exception as e:
            raise RuntimeError(
                f"Error when querying count data, {self._model_cls.__name__}: {e}"
            ) from e


class UpdateBuilder[T: ModelMixin](BaseBuilder[T]):
    def __init__(
        self,
        *,
        model_cls: type[T] | None = None,
        model_ins: T | None = None,
        session_provider: SessionProvider,
    ):
        # model_cls 和 model_ins 必须有一个不为 None
        if model_cls is None and model_ins is None:
            raise ValueError("UpdateBuilder requires either model_cls or model_ins")

        target_cls: type[T] = (
            model_cls if model_cls is not None else cast(type[T], model_ins.__class__)
        )
        super().__init__(target_cls, session_provider=session_provider)
        self._stmt = update(self._model_cls)
        self._update_dict = {}
        self._model_ins = model_ins
        if model_ins is not None:
            self._stmt = self._stmt.where(self._model_cls.id == model_ins.id)

    def update(
        self,
        updates: Mapping[ColumnKey, Any] | None = None,
        **kwargs: Any,
    ) -> Self:
        # 合并更新数据
        raw_updates: dict[ColumnKey, Any] = dict(updates or {})
        raw_updates |= kwargs

        for key, value in raw_updates.items():
            column_name = self._model_cls.normalize_update_column_name(key)

            if not self._model_cls.has_column(column_name):
                logger.warning(
                    f"{column_name} is not a {self._model_cls.__name__} column"
                )
                continue

            if isinstance(value, datetime) and value.tzinfo:
                value = value.replace(tzinfo=None)

            self._update_dict[column_name] = value

        return self

    def soft_delete(self) -> Self:
        if self._model_cls.has_deleted_at_column():
            self._update_dict[self._model_cls.deleted_at_column_name()] = (
                utc_now_naive()
            )

        return self

    @property
    def _update_stmt(self) -> Update:
        """获取 Update 语句，若类型不匹配则抛出异常"""
        if not isinstance(self._stmt, Update):
            raise RuntimeError("Statement is not an Update")
        return self._stmt

    @property
    def update_stmt(self) -> Update:
        if not self._update_dict:
            return self._update_stmt

        # 自动处理 updated_at 和 deleted_at 同步
        updated_col = self._model_cls.updated_at_column_name()
        deleted_col = self._model_cls.deleted_at_column_name()

        if deleted_col in self._update_dict:
            self._update_dict.setdefault(updated_col, self._update_dict[deleted_col])
        self._update_dict.setdefault(updated_col, utc_now_naive())

        if self._model_cls.has_updater_id_column():
            self._update_dict.setdefault(
                self._model_cls.updater_id_column_name(),
                context.get_user_id(),
            )

        # 自动同步更新字典到模型实例
        self._sync_update_dict_to_instance()

        return self._update_stmt.values(**self._update_dict).execution_options(
            synchronize_session=False
        )

    async def execute(self):
        if not self._update_dict:
            return

        try:
            async with self._session_provider() as sess:
                await sess.execute(self.update_stmt)
                await sess.commit()
        except Exception as e:
            raise RuntimeError(
                f"Error when updating data, {self._model_cls.__name__}: {e}"
            ) from e

    def _sync_update_dict_to_instance(self) -> None:
        """将更新字典同步到模型实例

        将 _update_dict 中的所有字段（包括自动添加的 updated_at、updater_id 等）
        同步到模型实例，保持内存对象与数据库状态一致。

        注意：该方法仅在 model_ins 存在时执行同步。
        """
        if self._model_ins is not None:
            for key, value in self._update_dict.items():
                setattr(self._model_ins, key, value)
