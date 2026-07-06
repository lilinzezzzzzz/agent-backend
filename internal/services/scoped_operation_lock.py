"""ScopedOperationLock 服务层。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from internal.core import AppException, errors
from internal.dao.scoped_operation_lock import (
    ScopedOperationLockDao,
    new_scoped_operation_lock_dao,
)
from internal.models.scoped_operation_lock import ScopedOperationLock


class ScopedOperationLockService:
    """ScopedOperationLock 业务服务。

    提供锁获取功能，校验参数并调用 DAO。
    """

    def __init__(self, *, dao: ScopedOperationLockDao):
        self._dao = dao

    async def acquire_lock(
        self,
        *,
        session: AsyncSession,
        operation_scope: str,
        resource_key: str,
        wait: bool = True,
        nowait: bool = False,
        skip_locked: bool = False,
        creator_id: int | None = None,
    ) -> ScopedOperationLock:
        """在事务内获取锁。

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
            AppException: 参数校验失败时抛出
            RuntimeError: 锁获取失败时抛出
        """
        # 参数校验
        self._validate_parameters(operation_scope, resource_key)

        # 互斥参数校验
        if nowait and skip_locked:
            raise AppException(
                errors.BadRequest,
                message="nowait 和 skip_locked 参数互斥，不能同时为 True",
            )

        return await self._dao.acquire(
            session,
            operation_scope,
            resource_key,
            wait=wait,
            nowait=nowait,
            skip_locked=skip_locked,
            creator_id=creator_id,
        )

    @staticmethod
    def _validate_parameters(operation_scope: str, resource_key: str) -> None:
        """校验锁参数。"""
        if not operation_scope or not operation_scope.strip():
            raise AppException(
                errors.BadRequest,
                message="operation_scope 不能为空",
            )

        if len(operation_scope) > 64:
            raise AppException(
                errors.BadRequest,
                message="operation_scope 长度不能超过 64 个字符",
            )

        if not resource_key or not resource_key.strip():
            raise AppException(
                errors.BadRequest,
                message="resource_key 不能为空",
            )

        if len(resource_key) > 128:
            raise AppException(
                errors.BadRequest,
                message="resource_key 长度不能超过 128 个字符",
            )


# 全局单例（懒加载）
_scoped_operation_lock_service: ScopedOperationLockService | None = None


def new_scoped_operation_lock_service() -> ScopedOperationLockService:
    """获取 ScopedOperationLockService 单例。"""
    global _scoped_operation_lock_service
    if _scoped_operation_lock_service is None:
        _scoped_operation_lock_service = ScopedOperationLockService(
            dao=new_scoped_operation_lock_dao(),
        )
    return _scoped_operation_lock_service
