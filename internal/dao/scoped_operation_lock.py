"""ScopedOperationLock 表行锁数据访问。"""

from __future__ import annotations

from sqlalchemy import Select, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from internal.infra.database import get_read_session, get_session
from internal.models.scoped_operation_lock import ScopedOperationLock
from pkg.database.audit import AuditActor
from pkg.database.dao import BaseDao
from pkg.ids import snowflake_id_generator
from pkg.toolkit.timer import utc_now_naive


_MYSQL_DIALECT_NAMES = {"mysql", "mariadb"}
_MYSQL_LOCK_UNAVAILABLE_ERROR_CODES = {
    1205,  # Lock wait timeout exceeded
    1213,  # Deadlock found
    3572,  # Statement aborted because NOWAIT lock could not be acquired
}
_INSERT_IGNORE_LOCK_ROW_STMT = text(
    """
    INSERT IGNORE INTO scoped_operation_locks
        (id, operation_scope, resource_key, creator_id, creator_type,
         created_at, updater_id, updater_type, updated_at, deleted_at)
    VALUES
        (:id, :operation_scope, :resource_key, :creator_id, :creator_type,
         :created_at, NULL, NULL, :updated_at, NULL)
    """
)


class ScopedOperationLockDao(BaseDao[ScopedOperationLock]):
    """封装基于 InnoDB 行锁的事务互斥锁。"""

    _model_cls: type[ScopedOperationLock] = ScopedOperationLock

    async def acquire(
        self,
        *,
        session: AsyncSession,
        operation_scope: str,
        resource_key: str,
        wait: bool,
        audit_actor: AuditActor | None = None,
    ) -> bool:
        """在当前显式事务内获取锁。

        实现流程：
        1. 使用 ``INSERT IGNORE`` 确保锁身份行存在；
        2. 使用 ``SELECT ... FOR UPDATE`` 锁定唯一身份行；
        3. 调用方事务 commit/rollback 后 InnoDB 自动释放行锁。

        Args:
            session: 已开启事务的 MySQL/MariaDB AsyncSession。
            operation_scope: 锁作用域。
            resource_key: scope 内资源键。
            wait: True 时等待锁；False 时使用 NOWAIT 尝试获取锁。
            audit_actor: 首次创建锁身份行时写入的审计主体；默认 system。

        Returns:
            是否成功获取锁。等待模式正常返回时恒为 True。

        Raises:
            RuntimeError: session 未开启事务、数据库类型不满足要求或锁行异常缺失。
            DBAPIError: 等待模式下数据库锁错误或其他数据库错误。
        """
        self._validate_session(session)
        await self._ensure_lock_row_exists(
            session=session,
            operation_scope=operation_scope,
            resource_key=resource_key,
            audit_actor=audit_actor,
        )

        try:
            result = await session.execute(
                self._build_for_update_query(
                    operation_scope=operation_scope,
                    resource_key=resource_key,
                    wait=wait,
                )
            )
        except DBAPIError as exc:
            if not wait and self._is_lock_unavailable_error(exc):
                return False
            raise

        lock = result.scalar_one_or_none()
        if lock is None:
            if not wait:
                return False
            raise RuntimeError(
                f"Failed to acquire scoped operation lock: scope={operation_scope}, key={resource_key}"
            )

        return True

    async def _ensure_lock_row_exists(
        self,
        *,
        session: AsyncSession,
        operation_scope: str,
        resource_key: str,
        audit_actor: AuditActor | None,
    ) -> None:
        """确保锁身份行存在；重复身份不修改既有行。"""
        now = utc_now_naive()
        actor = audit_actor or AuditActor.system()
        await session.execute(
            _INSERT_IGNORE_LOCK_ROW_STMT,
            {
                "id": snowflake_id_generator.generate(),
                "operation_scope": operation_scope,
                "resource_key": resource_key,
                **actor.creator_values(),
                "created_at": now,
                "updated_at": now,
            },
        )

    def _build_for_update_query(
        self,
        *,
        operation_scope: str,
        resource_key: str,
        wait: bool,
    ) -> Select[tuple[ScopedOperationLock]]:
        """构建锁定唯一锁身份行的查询。"""
        stmt = select(self.model_cls).where(
            self.model_cls.operation_scope == operation_scope,
            self.model_cls.resource_key == resource_key,
        )
        return stmt.with_for_update(nowait=not wait)

    @staticmethod
    def _validate_session(session: AsyncSession) -> None:
        """校验锁必须运行在显式 MySQL/MariaDB 事务内。"""
        if not session.in_transaction():
            raise RuntimeError("Scoped operation lock requires an active transaction")

        dialect_name = session.get_bind().dialect.name
        if dialect_name not in _MYSQL_DIALECT_NAMES:
            raise RuntimeError(
                f"Scoped operation lock requires MySQL/MariaDB, got {dialect_name}"
            )

    @staticmethod
    def _is_lock_unavailable_error(exc: DBAPIError) -> bool:
        """识别 NOWAIT 未获取锁等可转换为 False 的数据库错误。"""
        orig = exc.orig
        error_code = getattr(orig, "args", [None])[0]
        return error_code in _MYSQL_LOCK_UNAVAILABLE_ERROR_CODES


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
