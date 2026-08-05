from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from pkg.database.base import ModelMixin


class ExternalIdentity(ModelMixin):
    """用户与外部认证提供方之间的稳定身份映射。"""

    __tablename__ = "external_identity"

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="关联 user.id 的逻辑外键",
    )
    provider: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="认证提供方，如 wechat、google、github",
    )
    connection_key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="稳定的认证连接标识，不存储 client secret",
    )
    subject: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        comment="认证连接内稳定且大小写敏感的用户标识",
    )
    email_snapshot: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="最近一次认证返回的邮箱快照，不作为身份主键",
    )
    union_id: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
        comment="提供方返回的跨应用标识快照，如微信 UnionID",
    )
    nickname_snapshot: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="最近一次认证返回的昵称快照，不作为身份主键",
    )
    avatar_snapshot: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        comment="最近一次认证返回的头像 URL 快照",
    )
    last_authenticated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        comment="最近认证成功时间，naive UTC",
    )

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "connection_key",
            "subject",
            name="uq_external_identity_subject",
        ),
        Index(
            "idx_external_identity_user_provider",
            "user_id",
            "provider",
            "deleted_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ExternalIdentity(id={self.id}, user_id={self.user_id}, "
            f"provider={self.provider!r}, connection_key={self.connection_key!r})>"
        )
