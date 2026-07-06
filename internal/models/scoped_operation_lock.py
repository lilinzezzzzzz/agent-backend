from __future__ import annotations

from sqlalchemy import BigInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from pkg.database.base import ModelMixin


class ScopedOperationLock(ModelMixin):
    """通用事务锁行。

    锁身份由 `(operation_scope, resource_key)` 唯一确定。
    本表不保存业务状态；锁有效期由持有行锁的数据库事务决定。
    """

    __tablename__ = "scoped_operation_locks"
    __table_args__ = (
        UniqueConstraint(
            "operation_scope",
            "resource_key",
            name="uk_scoped_op_lock_key",
        ),
        {"comment": "通用事务互斥锁"},
    )

    # 重写 creator_id 为可空，因为 worker/scheduler 没有真实用户上下文
    creator_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    operation_scope: Mapped[str] = mapped_column(String(64), nullable=False, comment="锁作用域")
    resource_key: Mapped[str] = mapped_column(String(128), nullable=False, comment="scope 内资源键")

    # 禁用软删除语义
    @classmethod
    def has_deleted_at_column(cls) -> bool:
        """锁表不支持软删除。"""
        return False

    def build_soft_delete_stmt(self):
        """锁表不支持软删除操作。"""
        raise NotImplementedError("ScopedOperationLock does not support soft delete")

    def build_restore_stmt(self):
        """锁表不支持恢复操作。"""
        raise NotImplementedError("ScopedOperationLock does not support restore")
