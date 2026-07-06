from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from internal.infra.database import get_read_session, get_session
from internal.models.scoped_operation_lock import ScopedOperationLock
from pkg.database.dao import BaseDao


class ScopedOperationLockDao(BaseDao[ScopedOperationLock]):
    """ScopedOperationLock 数据访问对象。

    提供事务内锁获取功能，不自行管理事务边界。
    """

    _model_cls: type[ScopedOperationLock] = ScopedOperationLock

    async def acquire(
        self,
        session: AsyncSession,
        operation_scope: str,
        resource_key: str,
        *,
        wait: bool = True,
        nowait: bool = False,
        skip_locked: bool = False,
        creator_id: int | None = None,
    ) -> ScopedOperationLock:
        """在事务内获取锁行。

        流程：
        1. 确保锁行存在（PostgreSQL 的 INSERT ... ON CONFLICT DO NOTHING）
        2. 对该行 SELECT ... FOR UPDATE
        3. 不提交事务，锁随调用方事务 commit/rollback 释放

        Args:
            session: 调用方传入的 AsyncSession，必须在事务内
            operation_scope: 锁作用域（如 "order_confirm"）
            resource_key: scope 内资源键（如 "order:123"）
            wait: 是否等待锁（默认 True）
            nowait: 是否立即失败（默认 False）
            skip_locked: 是否跳过已锁定行（默认 False）
            creator_id: 创建人 ID（可选）

        Returns:
            锁定的 ScopedOperationLock 实例

        Raises:
            RuntimeError: 锁获取失败时抛出
        """
        # 1. 确保锁行存在
        await self._ensure_lock_row_exists(session, operation_scope, resource_key, creator_id)

        # 2. 构建 SELECT ... FOR UPDATE 查询
        for_update_query = self._build_for_update_query(
            operation_scope, resource_key, wait=wait, nowait=nowait, skip_locked=skip_locked
        )

        # 3. 执行查询获取锁
        result = await session.execute(for_update_query)
        row = result.first()

        if row is None:
            raise RuntimeError(
                f"Failed to acquire lock for scope={operation_scope}, key={resource_key}"
            )

        # 4. 返回锁实例
        return row[0]

    async def _ensure_lock_row_exists(
        self,
        session: AsyncSession,
        operation_scope: str,
        resource_key: str,
        creator_id: int | None = None,
    ) -> None:
        """确保锁行存在，使用 PostgreSQL 的 INSERT ... ON CONFLICT DO NOTHING。"""
        await self._upsert_postgresql(session, operation_scope, resource_key, creator_id)

    async def _upsert_postgresql(
        self,
        session: AsyncSession,
        operation_scope: str,
        resource_key: str,
        creator_id: int | None = None,
    ) -> None:
        """PostgreSQL: INSERT ... ON CONFLICT DO NOTHING"""
        # 生成雪花 ID 和时间戳
        from pkg.ids import snowflake_id_generator
        from pkg.toolkit.timer import utc_now_naive

        lock_id = snowflake_id_generator.generate()
        now = utc_now_naive()

        stmt = text("""
            INSERT INTO scoped_operation_locks (id, operation_scope, resource_key, creator_id, created_at, updated_at)
            VALUES (:id, :scope, :key, :creator_id, :created_at, :updated_at)
            ON CONFLICT (operation_scope, resource_key) DO NOTHING
        """)
        await session.execute(stmt, {
            "id": lock_id,
            "scope": operation_scope,
            "key": resource_key,
            "creator_id": creator_id,
            "created_at": now,
            "updated_at": now,
        })

    def _build_for_update_query(
        self,
        operation_scope: str,
        resource_key: str,
        *,
        wait: bool = True,
        nowait: bool = False,
        skip_locked: bool = False,
    ):
        """构建 SELECT ... FOR UPDATE 查询。"""
        from sqlalchemy import select

        stmt = select(self.model_cls).where(
            self.model_cls.operation_scope == operation_scope,
            self.model_cls.resource_key == resource_key,
        )

        # 构建 FOR UPDATE 子句
        if nowait:
            stmt = stmt.with_for_update(nowait=True)
        elif skip_locked:
            stmt = stmt.with_for_update(skip_locked=True)
        elif not wait:
            # 如果不等待，使用 nowait
            stmt = stmt.with_for_update(nowait=True)
        else:
            # 默认等待
            stmt = stmt.with_for_update()

        return stmt


# 全局单例（懒加载）
_scoped_operation_lock_dao: ScopedOperationLockDao | None = None


def new_scoped_operation_lock_dao() -> ScopedOperationLockDao:
    """获取 ScopedOperationLockDao 单例。"""
    global _scoped_operation_lock_dao
    if _scoped_operation_lock_dao is None:
        _scoped_operation_lock_dao = ScopedOperationLockDao(
            session_provider=get_session,
            read_session_provider=get_read_session,
        )
    return _scoped_operation_lock_dao
