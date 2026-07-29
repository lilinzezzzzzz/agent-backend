from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from pkg.database.base import ModelMixin
from pkg.database.types import JSONType


class ThirdPartyAccount(ModelMixin):
    """第三方账号关联表

    用于存储用户与第三方平台（微信、支付宝、Google、GitHub 等）的账号绑定关系

    ```sql
    CREATE TABLE `third_party_account` (
        `id` BIGINT NOT NULL COMMENT '主键 ID',
        `user_id` INT NOT NULL COMMENT '用户 ID (逻辑外键)',
        `platform` VARCHAR(32) NOT NULL COMMENT '平台名称 (wechat, alipay, google, github 等)',
        `open_id` VARCHAR(256) NOT NULL COMMENT '平台唯一标识',
        `union_id` VARCHAR(256) DEFAULT NULL COMMENT '平台 UnionID',
        `avatar` VARCHAR(512) DEFAULT NULL COMMENT '头像 URL',
        `nickname` VARCHAR(128) DEFAULT NULL COMMENT '昵称',
        `access_token` VARCHAR(512) DEFAULT NULL COMMENT '访问令牌',
        `refresh_token` VARCHAR(512) DEFAULT NULL COMMENT '刷新令牌',
        `expires_at` DATETIME DEFAULT NULL COMMENT '令牌过期时间',
        `extra_data` JSON DEFAULT NULL COMMENT '额外信息 (使用 JSONType)',
        `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        `is_deleted` TINYINT NOT NULL DEFAULT 0 COMMENT '是否删除',
        `deleted_at` DATETIME DEFAULT NULL COMMENT '删除时间',
        PRIMARY KEY (`id`),
        UNIQUE KEY `uq_platform_openid` (`platform`, `open_id`),
        INDEX `idx_user_id` (`user_id`),
        INDEX `idx_platform` (`platform`),
        INDEX `idx_open_id` (`open_id`),
        INDEX `idx_union_id` (`union_id`),
        INDEX `idx_user_platform` (`user_id`, `platform`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='第三方账号关联表';
    ```
    """

    __tablename__ = "third_party_account"

    # 逻辑外键（不使用数据库外键约束）
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        comment="用户 ID",
        index=True
    )

    # 平台信息
    platform: Mapped[str] = mapped_column(
        String(32),
        comment="平台名称",
        index=True
    )  # wechat, alipay, google, github, apple 等

    # 第三方平台的唯一标识
    open_id: Mapped[str] = mapped_column(
        String(256),
        comment="平台唯一标识",
        index=True
    )

    # 可选的 UnionID（某些平台有）
    union_id: Mapped[str | None] = mapped_column(
        String(256),
        comment="平台 UnionID",
        nullable=True,
        index=True
    )

    # 用户信息
    avatar: Mapped[str | None] = mapped_column(
        String(512),
        comment="头像 URL",
        nullable=True
    )

    nickname: Mapped[str | None] = mapped_column(
        String(128),
        comment="昵称",
        nullable=True
    )

    # 令牌信息（可选，用于调用第三方 API）
    access_token: Mapped[str | None] = mapped_column(
        String(512),
        comment="访问令牌",
        nullable=True
    )

    refresh_token: Mapped[str | None] = mapped_column(
        String(512),
        comment="刷新令牌",
        nullable=True
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        comment="令牌过期时间",
        nullable=True
    )

    # 额外信息（JSON 格式，存储平台特定的数据）
    extra_data: Mapped[dict | None] = mapped_column(
        JSONType(),
        comment="额外信息",
        nullable=True
    )

    # 索引和约束
    __table_args__ = (
        # 确保同一平台同一用户的 open_id 唯一
        UniqueConstraint('platform', 'open_id', name='uq_platform_openid'),
        # 联合索引：快速查询用户的所有第三方账号
        Index('idx_user_platform', 'user_id', 'platform'),
    )

    def __repr__(self) -> str:
        return f"<ThirdPartyAccount(user_id={self.user_id}, platform={self.platform}, open_id={self.open_id})>"
