from pathlib import Path

import oss2

from pkg.oss.base import BaseStorage, StorageType, register_storage
from pkg.concurrency import anyio_run_in_thread


@register_storage(StorageType.ALIYUN)
class AliyunOSSBackend(BaseStorage):
    """
    阿里云 OSS 存储后端实现。
    已自动注册到 _STORAGE_REGISTRY。
    """

    def __init__(
        self,
        *,
        bucket_name: str,
        access_key: str,
        secret_key: str,
        endpoint: str | None = None,
        region: str | None = None,
    ):
        if not access_key or not secret_key:
            raise ValueError("Aliyun OSS requires access_key and secret_key")

        auth = oss2.Auth(access_key, secret_key)
        # 阿里云 endpoint 处理逻辑
        real_endpoint = endpoint if endpoint else f"oss-{region}.aliyuncs.com"
        # 确保 protocol 存在 (oss2 需要 http:// 或 https://)
        if not real_endpoint.startswith("http"):
            real_endpoint = f"https://{real_endpoint}"

        self.bucket = oss2.Bucket(auth, real_endpoint, bucket_name)

    async def upload(self, file_obj, path: str, content_type: str | None = None) -> str:
        headers = {"Content-Type": content_type} if content_type else None

        def _sync_upload():
            try:
                # 1. 路径上传
                if isinstance(file_obj, (str, Path)):
                    self.bucket.put_object_from_file(path, str(file_obj), headers=headers)
                # 2. Bytes 上传
                elif isinstance(file_obj, bytes):
                    self.bucket.put_object(path, file_obj, headers=headers)
                # 3. FastAPI UploadFile 或 文件对象
                else:
                    # 兼容 FastAPI UploadFile
                    real_file = file_obj.file if hasattr(file_obj, "file") else file_obj

                    if hasattr(real_file, "seek"):
                        real_file.seek(0)

                    self.bucket.put_object(path, real_file, headers=headers)

            except oss2.exceptions.OssError as e:
                raise Exception(f"Aliyun OSS Upload failed: {e}") from e

        await anyio_run_in_thread(_sync_upload)
        # 阿里云签名 URL (或根据需求直接拼接 CDN 域名)
        return await anyio_run_in_thread(self.bucket.sign_url, "GET", path, 3600)

    async def generate_presigned_url(self, path: str, expiration: int = 3600) -> str:
        return await anyio_run_in_thread(self.bucket.sign_url, "GET", path, expiration)

    async def delete(self, path: str) -> bool:
        result = await anyio_run_in_thread(self.bucket.delete_object, path)
        return result.status == 204

    async def exists(self, path: str) -> bool:
        return await anyio_run_in_thread(self.bucket.object_exists, path)
