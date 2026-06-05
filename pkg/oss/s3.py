import io
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from pkg.oss.base import BaseStorage, StorageType, register_storage
from pkg.concurrency import anyio_run_in_thread


@register_storage(StorageType.S3)
class S3Backend(BaseStorage):
    """
    AWS S3 / S3 兼容存储后端实现。
    已自动注册到 _STORAGE_REGISTRY。
    """

    def __init__(
        self, bucket_name: str, access_key: str, secret_key: str, endpoint: str | None = None, region: str | None = None
    ):
        self.bucket_name = bucket_name
        self.endpoint = endpoint
        self.client = boto3.client(
            "s3",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            endpoint_url=endpoint,
        )

    async def upload(self, file_obj, path: str, content_type: str | None = None) -> str:
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type

        def _sync_upload():
            try:
                # 1. 路径上传
                if isinstance(file_obj, (str, Path)):
                    self.client.upload_file(str(file_obj), self.bucket_name, path, ExtraArgs=extra_args)

                # 2. Bytes / 文件对象
                else:
                    # 兼容 FastAPI UploadFile
                    real_file = file_obj.file if hasattr(file_obj, "file") else file_obj

                    if isinstance(real_file, bytes):
                        # Boto3 upload_fileobj 需要 file-like object，所以这里要把 bytes 包装
                        data_stream = io.BytesIO(real_file)
                    else:
                        data_stream = real_file
                        if hasattr(data_stream, "seek"):
                            data_stream.seek(0)

                    self.client.upload_fileobj(data_stream, self.bucket_name, path, ExtraArgs=extra_args)

            except ClientError as e:
                raise Exception(f"S3 Upload failed: {str(e)}") from e

        await anyio_run_in_thread(_sync_upload)

        # 返回逻辑
        if self.endpoint:
            return f"{self.endpoint}/{self.bucket_name}/{path}"
        return f"https://{self.bucket_name}.s3.amazonaws.com/{path}"

    async def generate_presigned_url(self, path: str, expiration: int = 3600) -> str:
        def _gen():
            return self.client.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket_name, "Key": path}, ExpiresIn=expiration
            )

        return await anyio_run_in_thread(_gen)

    async def delete(self, path: str) -> bool:
        await anyio_run_in_thread(self.client.delete_object, Bucket=self.bucket_name, Key=path)
        return True

    async def exists(self, path: str) -> bool:
        try:
            await anyio_run_in_thread(self.client.head_object, Bucket=self.bucket_name, Key=path)
            return True
        except ClientError:
            return False
