import hashlib
import socket
import uuid
from typing import cast

import uuid6
from snowflake import SnowflakeGenerator

from pkg.logger import logger


def uuid6_unique_str_id() -> str:
    """Generate a time-sortable UUIDv7 hex string."""
    return uuid6.uuid7().hex


def auto_snowflake_node_id() -> int:
    """基于机器信息自动生成 node_id (0-1023)"""
    try:
        mac = uuid.getnode()
        return mac % 1024
    except Exception as e:
        logger.warning(f"Failed to get mac address: {e}")
        hostname = socket.gethostname()
        return int(hashlib.md5(hostname.encode()).hexdigest(), 16) % 1024


class SnowflakeIDGenerator:
    """Snowflake ID 生成器工具类"""

    def __init__(self, node_id: int):
        if not node_id:
            raise ValueError("node_id cannot be empty")
        self._generator = SnowflakeGenerator(node_id)

    def generate(self) -> int:
        # SnowflakeGenerator 是无限生成器，next() 永远不会返回 None
        return cast(int, next(self._generator))

    def generate_batch(self, count: int) -> list[int]:
        # SnowflakeGenerator 是无限生成器，next() 永远不会返回 None
        return [cast(int, next(self._generator)) for _ in range(count)]


snowflake_id_generator = SnowflakeIDGenerator(node_id=auto_snowflake_node_id())


def uuid6_unique_int_id() -> int:
    """
    使用 UUIDv7 标准 (uuid6.uuid7) 生成一个 64 位的通用、时间排序的整数 ID。

    UUIDv7 确保了 ID 的时间可排序性，这在数据库索引中非常有用。
    该函数返回的是 128 位 UUID 的高 64 位。

    返回值:
        int: 生成的 64 位整数 ID (时间排序)。
    """
    # 1. 生成一个 128 位的 UUIDv7 对象
    # 2. .int 获取其 128 位整数表示
    # 3. >> 64 取高 64 位作为 ID，因为 UUIDv7 的时间戳信息主要集中在高位
    unique_id = uuid6.uuid7().int >> 64
    return unique_id
