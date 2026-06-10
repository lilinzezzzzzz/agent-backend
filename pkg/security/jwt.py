from datetime import UTC, datetime, timedelta

import jwt
from loguru import logger


class JWTHandler:
    def __init__(self, secret: str, algorithm: str = "HS256", expire_minutes: int = 30):
        self.secret = secret
        self.algorithm = algorithm
        self.expire_minutes = expire_minutes

    def verify_token(self, token: str) -> tuple[int | None, bool]:
        """
        验证 Token = request.headers.get("Authorization")
        """
        if not token or not token.startswith("Bearer "):
            return None, False

        token = token.split(" ")[1]
        try:
            payload = jwt.decode(token, self.secret, algorithms=[self.algorithm])
            user_id = payload.get("user_id")
            if user_id is None:
                logger.warning("Token verification failed: user_id not found")
                return None, False
        except jwt.ExpiredSignatureError:
            logger.warning("Token verification failed: token expired")
            return None, False
        except jwt.InvalidTokenError:
            logger.warning("Token verification failed: invalid token")
            return None, False

        return user_id, True

    def create_token(self, user_id: int, username: str, expire_minutes: int | None = None) -> str:
        exp_minutes = expire_minutes or self.expire_minutes
        expiration = datetime.now(UTC) + timedelta(minutes=exp_minutes)
        payload = {"username": username, "user_id": user_id, "exp": int(expiration.timestamp())}
        return jwt.encode(payload, self.secret, algorithm=self.algorithm)


"""
# 初始化（通常在配置或依赖注入时）
jwt_handler = JWTHandler(
    secret="your-secret-key",
    algorithm="HS256",
    expire_minutes=30
)

# 创建 token
token = jwt_handler.create_token(user_id=1, username="test_user")

# 验证 token
user_id, is_valid = await jwt_handler.verify_token("Bearer xxx")

"""
