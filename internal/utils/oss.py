from pkg.oss.aliyun import AliyunOSSBackend
from pkg.lazy_proxy import lazy_proxy

_aliyun_oss: AliyunOSSBackend | None = None


def init_aliyun_oss(bucket_name: str, access_key: str, secret_key: str, endpoint: str, region: str):
    global _aliyun_oss
    _aliyun_oss = AliyunOSSBackend(
        bucket_name=bucket_name, access_key=access_key, secret_key=secret_key, endpoint=endpoint, region=region
    )


def _get_aliyun_oss() -> AliyunOSSBackend:
    if _aliyun_oss is None:
        raise RuntimeError("Aliyun OSS not initialized.")
    return _aliyun_oss


aliyun_oss = lazy_proxy(_get_aliyun_oss)
