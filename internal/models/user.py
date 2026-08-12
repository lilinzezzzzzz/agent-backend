from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from pkg.database.base import ModelMixin


class User(ModelMixin):
    """用户表；共享主键和审计字段由 ``ModelMixin`` 提供。"""

    __tablename__ = "user"

    username: Mapped[str] = mapped_column(String(64), comment="用户名")
    account: Mapped[str] = mapped_column(String(64), comment="账号")
    phone: Mapped[str] = mapped_column(String(11), comment="手机号")
    password_hash: Mapped[str | None] = mapped_column(String(255), comment="密码哈希")
