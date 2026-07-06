"""ScopedOperationLock ORM model。"""

from __future__ import annotations

from sqlalchemy import BigInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from pkg.database.base import ModelMixin


class ScopedOperationLock(ModelMixin):
    """通用事务锁身份行。

    本表只保存锁身份；实际锁状态由持有该行 ``FOR UPDATE`` 锁的数据库事务决定。
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

    # worker/scheduler 等后台任务没有真实用户上下文，锁身份行允许无创建人。
    creator_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=True,
        default=None,
        comment="创建人 ID",
    )
    operation_scope: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="锁作用域",
    )
    resource_key: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="scope 内资源键",
    )

    @classmethod
    def has_deleted_at_column(cls) -> bool:
        """锁身份行不参与通用软删除过滤。"""
        return False

    def build_soft_delete_stmt(self):
        """锁身份行不支持软删除。"""
        raise NotImplementedError("ScopedOperationLock does not support soft delete")

    def build_restore_stmt(self):
        """锁身份行不支持恢复。"""
        raise NotImplementedError("ScopedOperationLock does not support restore")
