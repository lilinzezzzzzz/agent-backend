"""内部通用类型定义

本模块包含跨模块使用的枚举类、类型别名和全局常量
"""

from enum import StrEnum
from uuid import UUID


class UserStatus(StrEnum):
    """用户状态枚举"""

    ACTIVE = "active"  # 活跃
    INACTIVE = "inactive"  # 不活跃
    BANNED = "banned"  # 已封禁
    DELETED = "deleted"  # 已删除


class TokenType(StrEnum):
    """Token 类型枚举"""

    ACCESS = "access"  # 访问令牌
    REFRESH = "refresh"  # 刷新令牌
    VERIFY_EMAIL = "verify_email"  # 邮箱验证令牌
    RESET_PASSWORD = "reset_password"  # 重置密码令牌


class CachePrefix:
    """Redis 缓存键前缀常量

    使用示例:
        key = f"{CachePrefix.USER}:{user_id}"
        # 结果："user:123456"
    """

    USER = "user"  # 用户信息
    TOKEN = "token"  # Token
    TOKEN_LIST = "token_list"  # 用户 Token 列表
    LOCK = "lock"  # 分布式锁
    SESSION = "session"  # 会话
    CONFIG = "config"  # 配置
    RATE_LIMIT = "rate_limit"  # 限流


class LockKey:
    """分布式锁键名生成

    使用示例:
        from internal.utils.types import LockKey

        # 生成用户操作锁
        user_id = UUID("019ff4e4-ea48-7af4-8dd0-4b14c87cc02c")
        lock_key = LockKey.user_operation(user_id=user_id)
    """

    @staticmethod
    def user_operation(user_id: UUID) -> str:
        """用户操作锁"""
        return f"{CachePrefix.LOCK}:user_op:{user_id}"

    @staticmethod
    def resource_access(resource_type: str, resource_id: int) -> str:
        """资源访问锁"""
        return f"{CachePrefix.LOCK}:resource:{resource_type}:{resource_id}"

    @staticmethod
    def distributed_task(task_name: str) -> str:
        """分布式任务锁"""
        return f"{CachePrefix.LOCK}:task:{task_name}"


# 全局常量
DEFAULT_PAGE_SIZE = 10  # 默认分页大小
MAX_PAGE_SIZE = 100  # 最大分页大小
DEFAULT_TOKEN_EXPIRY = 86400  # 默认 Token 过期时间 (秒，24 小时)
REFRESH_TOKEN_EXPIRY = 604800  # 刷新 Token 过期时间 (秒，7 天)
