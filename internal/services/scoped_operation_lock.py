"""Scoped operation transaction lock 服务。"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from internal.core import AppException, errors
from internal.dao.scoped_operation_lock import (
    ScopedOperationLockDao,
    new_scoped_operation_lock_dao,
)
from pkg.database.base import AuditActor


class LockMode(StrEnum):
    """事务锁等待策略。"""

    WAIT = "wait"
    TRY = "try"


class ScopedOperationLockService:
    """提供基于数据库事务行锁的业务互斥锁。"""

    def __init__(self, *, dao: ScopedOperationLockDao):
        self._dao = dao

    async def acquire_lock(
        self,
        *,
        session: AsyncSession,
        operation_scope: str,
        resource_key: str,
        mode: LockMode = LockMode.WAIT,
        audit_actor: AuditActor | None = None,
    ) -> bool:
        """在当前事务内获取锁。

        锁由 ``scoped_operation_locks`` 唯一身份行上的 ``SELECT ... FOR UPDATE``
        实现，并在事务 commit 或 rollback 时自动释放。业务读写必须使用同一个
        session 和事务。

        Args:
            session: 已开启事务的 MySQL/MariaDB AsyncSession。
            operation_scope: 锁作用域，如 ``order_confirm``。
            resource_key: scope 内资源键，如 ``order:123``。
            mode: WAIT 等待获取；TRY 立即返回是否获取成功。
            audit_actor: 首次创建锁身份行时写入的审计主体；默认 system。

        Returns:
            是否成功获取锁。WAIT 模式正常返回时恒为 True。

        Raises:
            AppException: 锁参数或 mode 无效。
            RuntimeError: session 事务或数据库类型不满足要求。
        """
        self._validate_parameters(operation_scope, resource_key)
        if not isinstance(mode, LockMode):
            raise AppException(errors.BadRequest, message="mode 必须是 LockMode")

        return await self._dao.acquire(
            session=session,
            operation_scope=operation_scope,
            resource_key=resource_key,
            wait=mode is LockMode.WAIT,
            audit_actor=audit_actor,
        )

    @staticmethod
    def _validate_parameters(operation_scope: str, resource_key: str) -> None:
        """校验锁参数。"""
        if not operation_scope or not operation_scope.strip():
            raise AppException(errors.BadRequest, message="operation_scope 不能为空")

        if len(operation_scope) > 64:
            raise AppException(
                errors.BadRequest,
                message="operation_scope 长度不能超过 64 个字符",
            )

        if not resource_key or not resource_key.strip():
            raise AppException(errors.BadRequest, message="resource_key 不能为空")

        if len(resource_key) > 128:
            raise AppException(
                errors.BadRequest,
                message="resource_key 长度不能超过 128 个字符",
            )


_scoped_operation_lock_service: ScopedOperationLockService | None = None


def new_scoped_operation_lock_service() -> ScopedOperationLockService:
    """获取 ScopedOperationLockService 单例。"""
    global _scoped_operation_lock_service
    if _scoped_operation_lock_service is None:
        _scoped_operation_lock_service = ScopedOperationLockService(
            dao=new_scoped_operation_lock_dao(),
        )
    return _scoped_operation_lock_service
