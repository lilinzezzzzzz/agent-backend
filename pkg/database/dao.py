from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from sqlalchemy import Executable, Insert, Subquery, distinct, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, aliased

from pkg.database.base import ModelMixin, SessionProvider
from pkg.database.builder import CountBuilder, QueryBuilder, UpdateBuilder
from pkg.database.types import ColumnKey

"""
数据访问对象 (DAO)
"""


class BaseDao[T: ModelMixin]:
    """
    数据访问对象基类，提供通用的 CRUD 操作。

    建议:
        在子类中访问模型字段时，建议使用 self.model_cls 而非直接引用具体的模型类名。

        例如: self.model_cls.phone 而非 User.phone
    """

    _model_cls: type[T]

    # ==========================================================================
    # 初始化与属性
    # ==========================================================================

    def __init__(
        self,
        *,
        session_provider: SessionProvider,
        read_session_provider: SessionProvider | None = None,
    ):
        """
        Args:
            session_provider: 写库 session 提供者（主库）
            read_session_provider: 读库 session 提供者（只读副本）。
                如果为 None，读操作自动 fallback 到写库的 session_provider。
        """
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
        """获取写库 session 提供者（主库）"""
        return self._session_provider

    @property
    def read_session_provider(self) -> SessionProvider:
        """获取读库 session 提供者（只读副本），如果未配置则返回写库 session_provider"""
        return self._read_session_provider

    def _assert_instance_model_match(self, instance: ModelMixin) -> None:
        if instance.__class__ is not self.model_cls:
            raise TypeError(
                f"{self.__class__.__name__} expects instance of {self.model_cls.__name__}, "
                f"got {instance.__class__.__name__}"
            )

    def _assert_instances_model_match(self, items: list[T]) -> None:
        for item in items:
            self._assert_instance_model_match(item)

    # ==========================================================================
    # 工厂方法
    # ==========================================================================

    def create(self, **kwargs) -> T:
        return self.model_cls.create(**kwargs)

    # ==========================================================================
    # 查询器（读库）
    # ==========================================================================

    @property
    def querier(self) -> QueryBuilder[T]:
        """默认查询器（排除已删除，按更新时间倒序）"""
        return QueryBuilder(
            self.model_cls,
            session_provider=self._read_session_provider,
            include_deleted=False,
        ).desc_(self.model_cls.updated_at)

    @property
    def querier_inc_deleted(self) -> QueryBuilder[T]:
        """包含已删除记录的查询器（按更新时间倒序）"""
        return QueryBuilder(
            self.model_cls,
            session_provider=self._read_session_provider,
            include_deleted=True,
        ).desc_(self.model_cls.updated_at)

    @property
    def querier_unsorted(self) -> QueryBuilder[T]:
        """无排序查询器（排除已删除）"""
        return QueryBuilder(
            self.model_cls,
            session_provider=self._read_session_provider,
            include_deleted=False,
        )

    @property
    def querier_inc_deleted_unsorted(self) -> QueryBuilder[T]:
        """无排序查询器（包含已删除）"""
        return QueryBuilder(
            self.model_cls,
            session_provider=self._read_session_provider,
            include_deleted=True,
        )

    def col_querier(
        self,
        *columns: InstrumentedAttribute,
        include_deleted: bool = False,
    ) -> QueryBuilder[T]:
        """创建只查询指定列的查询器

        Args:
            columns: 要查询的列
            include_deleted: 是否包含已删除记录

        Returns:
            QueryBuilder，只查询指定列（无默认排序）

        Example:
            # 只查询 id 列
            ids = await dao.col_querier(Model.id).eq_(Model.org_id, 1).values()

            # 查询多列
            rows = await dao.col_querier(Model.id, Model.name).eq_(...).values()
        """
        custom_stmt = select(*columns).select_from(self.model_cls) if columns else None

        return QueryBuilder(
            self.model_cls,
            session_provider=self._read_session_provider,
            include_deleted=include_deleted,
            custom_stmt=custom_stmt,
        )

    def sub_querier(self, subquery: Subquery) -> QueryBuilder[T]:
        """创建子查询查询器"""
        alias = aliased(self.model_cls, subquery)
        return QueryBuilder(
            self.model_cls,
            session_provider=self._read_session_provider,
            custom_stmt=select(alias),
        )

    # ==========================================================================
    # 查询器（写库 - 强制读主库，用于写后读一致性场景）
    # ==========================================================================

    @property
    def write_querier(self) -> QueryBuilder[T]:
        """强制从主库查询（按更新时间倒序）"""
        return QueryBuilder(
            self.model_cls,
            session_provider=self._session_provider,
            include_deleted=False,
        ).desc_(self.model_cls.updated_at)

    @property
    def write_querier_unsorted(self) -> QueryBuilder[T]:
        """强制从主库查询（无排序）"""
        return QueryBuilder(
            self.model_cls,
            session_provider=self._session_provider,
            include_deleted=False,
        )

    # ==========================================================================
    # 计数器
    # ==========================================================================

    @property
    def counter(self) -> CountBuilder[T]:
        """默认计数器（排除已删除）"""
        return CountBuilder(
            self.model_cls,
            session_provider=self._session_provider,
            include_deleted=False,
        )

    @property
    def counter_inc_deleted(self) -> CountBuilder[T]:
        """包含已删除记录的计数器"""
        return CountBuilder(
            self.model_cls,
            session_provider=self._session_provider,
            include_deleted=True,
        )

    def col_counter(
        self,
        count_column: InstrumentedAttribute,
        *,
        is_distinct: bool = False,
        include_deleted: bool = False,
    ) -> CountBuilder[T]:
        """创建统计指定列的计数器

        Args:
            count_column: 要统计的列
            is_distinct: 是否去重统计（COUNT DISTINCT）
            include_deleted: 是否包含已删除记录

        Returns:
            CountBuilder，统计指定列的数量

        Example:
            # 统计部门数量
            dept_count = await dao.col_counter(Model.dept_id, is_distinct=True).eq_(Model.org_id, 1).count()

            # 统计活跃用户数
            active_count = await dao.col_counter(Model.id).eq_(Model.status, "active").count()
        """
        target = distinct(count_column) if is_distinct else count_column
        expr = func.count(target)
        custom_stmt = select(expr).select_from(self.model_cls)

        return CountBuilder(
            self.model_cls,
            session_provider=self._session_provider,
            custom_stmt=custom_stmt,
            include_deleted=include_deleted,
        )

    # ==========================================================================
    # 更新器
    # ==========================================================================

    @property
    def updater(self) -> UpdateBuilder[T]:
        """创建更新构建器"""
        return UpdateBuilder(
            model_cls=self.model_cls, session_provider=self._session_provider
        )

    def ins_updater(self, ins: T) -> UpdateBuilder[T]:
        """创建基于实例的更新构建器"""
        self._assert_instance_model_match(ins)
        return UpdateBuilder(model_ins=ins, session_provider=self._session_provider)

    # ==========================================================================
    # 通用查询方法
    # ==========================================================================

    async def query_by_primary_id(
        self,
        primary_id: int,
        *,
        creator_id: int | None = None,
        include_deleted: bool = False,
    ) -> T | None:
        """根据主键 ID 查询单条记录"""
        qb = self.querier_inc_deleted if include_deleted else self.querier
        qb = qb.eq_(self.model_cls.id, primary_id)
        if creator_id and self.model_cls.has_creator_id_column():
            qb = qb.where(self.model_cls.get_creator_id_column() == creator_id)
        return await qb.first()

    async def query_by_ids(self, ids: list[int]) -> list[T]:
        """根据主键 ID 列表批量查询"""
        return await self.querier.in_(self.model_cls.id, ids).all()

    # ==========================================================================
    # 实例操作（执行模型构造出的写语句）
    # ==========================================================================

    async def _execute_stmt(
        self, stmt: Executable | None, *, error_context: str
    ) -> None:
        if stmt is None:
            return

        await self.model_cls.execute_stmt(
            stmt, self._session_provider, error_context=error_context
        )

    async def insert(self, instance: T) -> None:
        """插入新实例"""
        self._assert_instance_model_match(instance)
        await self._execute_stmt(
            instance.build_insert_stmt(),
            error_context=f"{self.model_cls.__name__} insert",
        )

    async def update(
        self,
        instance: T,
        updates: Mapping[ColumnKey, Any] | None = None,
        **kwargs: Any,
    ) -> T:
        """更新实例字段并持久化

        Args:
            instance: 要更新的实例
            updates: 要更新的字段映射，支持字符串列名或 InstrumentedAttribute
            **kwargs: 要更新的字段

        Returns:
            更新后的实例
        """
        self._assert_instance_model_match(instance)
        await self._execute_stmt(
            instance.build_update_stmt(updates=updates, **kwargs),
            error_context=f"{self.model_cls.__name__} update",
        )
        return instance

    async def soft_delete(self, instance: T) -> None:
        """软删除实例"""
        self._assert_instance_model_match(instance)
        await self._execute_stmt(
            instance.build_soft_delete_stmt(),
            error_context=f"{self.model_cls.__name__} soft_delete",
        )

    async def restore(self, instance: T) -> None:
        """恢复已删除的实例"""
        self._assert_instance_model_match(instance)
        await self._execute_stmt(
            instance.build_restore_stmt(),
            error_context=f"{self.model_cls.__name__} restore",
        )

    # ==========================================================================
    # 批量操作 (Batch)
    # ==========================================================================

    def build_insert_rows_stmt(self, *, rows: list[dict[str, Any]]) -> Insert | None:
        """[Batch Dict] 构造批量插入字典的 INSERT 语句。"""
        if not rows:
            return None

        defaults = self.model_cls.get_context_defaults()
        db_values = [
            self.model_cls.fill_dict_insert_fields(row, defaults) for row in rows
        ]
        return insert(self.model_cls).values(db_values)

    async def insert_rows(self, *, rows: list[dict[str, Any]]) -> None:
        """[Batch Dict] 高性能批量插入字典。

        Args:
            rows: 要插入的字典列表

        Example:
            # 批量插入字典
            rows = [{"username": f"user{i}"} for i in range(10)]
            await dao.insert_rows(rows=rows)

            # 仅构建 SQL
            stmt = dao.build_insert_rows_stmt(rows=rows)
        """
        await self._execute_stmt(
            self.build_insert_rows_stmt(rows=rows),
            error_context=f"{self.model_cls.__name__} insert_rows",
        )

    def build_insert_instances_stmt(self, *, items: list[T]) -> Insert | None:
        """[Batch Instance] 构造批量插入对象实例的 INSERT 语句。"""
        if not items:
            return None

        self._assert_instances_model_match(items)
        db_values = [ins.prepare_insert_values() for ins in items]
        return insert(self.model_cls).values(db_values)

    async def insert_instances(self, *, items: list[T]) -> None:
        """[Batch Instance] 高性能批量插入对象实例。

        Args:
            items: 要插入的实例列表

        Example:
            # 批量插入实例
            users = [dao.create(username=f"user{i}") for i in range(10)]
            await dao.insert_instances(items=users)

            # 仅构建 SQL
            stmt = dao.build_insert_instances_stmt(items=users)
        """
        await self._execute_stmt(
            self.build_insert_instances_stmt(items=items),
            error_context=f"{self.model_cls.__name__} insert_instances",
        )


async def execute_transaction(
    session_provider: SessionProvider,
    callback: Callable[[AsyncSession], Awaitable[None]],
    autoflush: bool = True,
) -> None:
    """
    [Transaction] 手动事务执行器：通过回调函数在同一个事务中执行复杂逻辑。

    该方法解决了简单的批量执行无法处理 "先插入获取ID，再使用ID插入关联表" 的逻辑依赖问题。
    它会自动开启事务，并在回调执行完毕后提交；如果发生异常则自动回滚。

    Args:
        session_provider: Session 提供者
        callback: 包含业务逻辑的异步函数。接收当前事务的 `AsyncSession`。
        autoflush: 是否自动刷新（默认 True）。如果在事务中需要立即获取自增 ID，请保持为 True。

    Raises:
        RuntimeError: 当事务执行失败时抛出，并包含原始异常信息。

    Examples:
        **场景 1：使用 UpdateBuilder 进行批量更新**
        ```python
        async def _batch_update(sess: AsyncSession) -> None:
            # 使用 UpdateBuilder 构建 SQL 语句
            updater = dao.updater.eq_(
                Model.id, record_id
            ).update(
                status="active",
            )
            # 在事务的 session 中执行
            await sess.execute(updater.update_stmt)

        await execute_transaction(session_provider, _batch_update)
        ```

        **场景 2：混合 ORM 和原生 SQL**
        ```python
        async def _mixed_operations(sess: AsyncSession) -> None:
            # 1. ORM 添加新对象
            new_user = User(name="Alice")
            sess.add(new_user)
            await sess.flush()  # 获取自增 ID

            # 2. 使用 UpdateBuilder 批量更新
            updater = dao.updater.eq_(
                Log.user_id, new_user.id
            ).update(
                status="processed",
            )
            await sess.execute(updater.update_stmt)

        await execute_transaction(session_provider, _mixed_operations)
        ```

        **场景 3：先插入后关联（使用雪花算法 ID）**
        ```python
        async def _create_with_relation(sess: AsyncSession) -> None:
            # 1. 创建主记录（ID 通过雪花算法预先生成）
            order = Order.create(user_id=1, amount=100)
            # order.id 已经有值，无需 flush

            # 2. 直接使用 order.id 创建子记录
            item = OrderItem.create(order_id=order.id, product="Apple")

            # 3. 一起添加到 session
            sess.add(order)
            sess.add(item)

        await execute_transaction(session_provider, _create_with_relation)
        ```

        **场景 4：数据库自增 ID（需要 flush）**
        *仅当使用数据库自增 ID 时需要此场景*
        ```python
        async def _create_with_auto_increment(sess: AsyncSession) -> None:
            # 1. 创建主记录（ID 由数据库自增生成）
            order = OtherModel(user_id=1, amount=100)
            sess.add(order)

            # [关键] 刷新到数据库以获取自增 ID（此时并未 commit）
            await sess.flush()

            # 2. 使用获取的 ID 创建子记录
            item = RelatedModel(other_id=order.id, product="Apple")
            sess.add(item)

        await execute_transaction(session_provider, _create_with_auto_increment)
        ```
    """
    try:
        async with session_provider(autoflush=autoflush) as sess, sess.begin():
            await callback(sess)
    except Exception as e:
        raise RuntimeError(f"Transaction failed: {e}") from e
