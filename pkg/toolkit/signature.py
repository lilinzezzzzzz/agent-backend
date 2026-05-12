import hashlib
import hmac
import time
from typing import Any

from pkg.logger import logger


class SignatureAuthHandler:
    SUPPORTED_HASH_ALGOS = {"sha256", "sha1", "md5"}  # 可扩展

    def __init__(
        self,
        *,
        secret_key: str,
        hash_algorithm: str = "sha256",
        timestamp_tolerance: int = 300,
    ):
        """
        :param secret_key: 用于签名的密钥（建议环境变量管理）
        :param hash_algorithm: 哈希算法（支持 sha256/sha1/md5）
        :param timestamp_tolerance: 时间戳误差秒数，防止重放
        """
        self.secret_key = secret_key.encode("utf-8")
        if hash_algorithm not in self.SUPPORTED_HASH_ALGOS:
            raise ValueError(f"Unsupported hash_algorithm: {hash_algorithm}")
        self.hash_algorithm = hash_algorithm
        self.timestamp_tolerance = timestamp_tolerance
        logger.success(
            f"SignatureAuthHandler Init Successfully, secret_key={secret_key}, hash_algorithm={hash_algorithm}, timestamp_tolerance={timestamp_tolerance}"
        )

    def generate_signature(self, data: dict[str, Any]) -> str:
        """
        生成签名字符串
        :param data: 任意 k-v 数据
        :return: 签名字符串
        """
        try:
            # 保证所有 value 都转为字符串
            sorted_items = sorted((str(k), str(v)) for k, v in data.items())
            message = "&".join(f"{k}={v}" for k, v in sorted_items).encode("utf-8")
            signature = hmac.new(
                self.secret_key, message, getattr(hashlib, self.hash_algorithm)
            ).hexdigest()
            return signature
        except Exception as e:
            logger.error(f"generate_signature error: {e}, data={data}")
            raise

    def verify_signature(self, data: dict[str, Any], signature: str | None) -> bool:
        """
        验证签名
        :param data: 原始数据
        :param signature: 待校验签名
        :return: True/False
        """
        if not signature:
            logger.warning("Missing signature for verification")
            return False

        try:
            expected_signature = self.generate_signature(data)
            return hmac.compare_digest(expected_signature, signature)
        except Exception as e:
            logger.error(
                f"verify_signature error: {e}, data={data}, signature={signature}"
            )
            return False

    def verify_timestamp(self, request_time: str | None) -> bool:
        """
        校验 UTC 秒级时间戳是否过期
        :param request_time: 字符串类型的 UTC 秒级时间戳
        :return: True/False
        """
        try:
            if not request_time:
                return False
            int_request_time = int(request_time)
            int_current_time = int(time.time())
            # 绝对容忍误差，双向防止时钟不同步
            if abs(int_current_time - int_request_time) > self.timestamp_tolerance:
                logger.warning(
                    f"Timestamp not in tolerance, request_time: {int_request_time}, current_time: {int_current_time}, tolerance: {self.timestamp_tolerance}s"
                )
                return False
        except Exception as e:
            logger.error(f"verify_timestamp error: {e}, request_time={request_time}")
            return False
        return True

    def verify(
        self, x_signature: str | None, x_timestamp: str | None, x_nonce: str | None
    ) -> bool:
        """
        统一验签入口
        :param x_signature: 签名字符串
        :param x_timestamp: 时间戳（UTC秒）
        :param x_nonce: 随机串
        :return: True/False
        """
        if not self.verify_timestamp(x_timestamp):
            logger.warning(f"Timestamp check failed: {x_timestamp}")
            return False

        data = {"timestamp": x_timestamp, "nonce": x_nonce}
        if not self.verify_signature(data, x_signature):
            logger.warning(
                f"Signature check failed, data={data}, signature={x_signature}"
            )
            return False

        return True
