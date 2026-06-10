from typing import TYPE_CHECKING, Literal, overload

from pkg.oss import (
    aliyun as _aliyun_module,  # noqa: F401
    s3 as _s3_module,  # noqa: F401
)
from pkg.oss.base import _STORAGE_REGISTRY, BaseStorage, StorageType, register_storage

if TYPE_CHECKING:
    from pkg.oss.aliyun import AliyunOSSBackend
    from pkg.oss.s3 import S3Backend


@overload
def get_storage_class(storage_type: Literal[StorageType.ALIYUN]) -> type["AliyunOSSBackend"]: ...


@overload
def get_storage_class(storage_type: Literal[StorageType.S3]) -> type["S3Backend"]: ...


def get_storage_class(storage_type: StorageType) -> type[BaseStorage]:
    """
    根据存储类型枚举获取对应的存储后端类。
    业务层只需要调用这个函数。
    """
    storage_class = _STORAGE_REGISTRY.get(storage_type)
    if not storage_class:
        raise NotImplementedError(f"Storage type <{storage_type}> is not registered or implemented.")
    return storage_class


__all__ = ["BaseStorage", "StorageType", "register_storage", "get_storage_class"]
