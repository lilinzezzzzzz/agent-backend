"""应用配置模块

包含配置类定义、加载逻辑和全局配置实例
"""

from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from loguru import logger
from pydantic import (
    MySQLDsn,
    PostgresDsn,
    RedisDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from internal import BASE_DIR
from pkg.crypter.aes import aes_decrypt
from pkg.logger import LogFormat
from pkg.toolkit.types import lazy_proxy

# =========================================================
# 配置定义
# =========================================================

# 支持的数据库类型
DBType = Literal["mysql", "postgresql", "oracle"]
EndpointGuardSource = Literal["settings", "redis", "settings+redis"]

# 数据库驱动映射
DB_DRIVER_MAP: dict[str, str] = {
    "mysql": "mysql+aiomysql",
    "postgresql": "postgresql+asyncpg",
    "oracle": "oracle+oracledb",
}


class Settings(BaseSettings):
    """
    应用全局配置。
    """

    # --- 核心环境配置 ---
    APP_ENV: Literal["local", "dev", "test", "prod"]
    DEBUG: bool = False

    # --- 日志配置 ---
    LOG_FORMAT: LogFormat = LogFormat.TEXT  # 日志格式: TEXT 或 JSON

    # --- 密钥配置 ---
    AES_SECRET: SecretStr
    JWT_SECRET: SecretStr
    JWT_ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    ECHO_CONFIG: bool = False  # 是否打印配置信息 (调试用)

    # --- CORS ---
    BACKEND_CORS_ORIGINS: list[str] = ["*"]

    # --- 第三方登录 ---
    WECHAT_APP_ID: str = ""
    WECHAT_APP_SECRET: SecretStr = SecretStr("")

    # --- Database ---
    DB_TYPE: DBType  # 数据库类型: mysql, postgresql, oracle (必填)
    DB_HOST: str
    DB_PORT: int = 3306
    DB_USERNAME: str
    DB_PASSWORD: SecretStr
    DB_DATABASE: str
    DB_SERVICE_NAME: str = ""  # Oracle 专用: Service Name
    DB_ECHO: bool = False  # 是否输出 SQL 日志

    # --- Database Read Replica (可选，不配置则不启用读写分离) ---
    DB_READ_HOST: str | None = None
    DB_READ_PORT: int | None = None
    DB_READ_USERNAME: str | None = None
    DB_READ_PASSWORD: SecretStr | None = None
    DB_READ_DATABASE: str | None = None
    DB_READ_SERVICE_NAME: str | None = None  # Oracle 专用

    # --- Redis ---
    REDIS_HOST: str
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: SecretStr = SecretStr("")
    REDIS_DB: int = 0

    # --- Endpoint Guard ---
    ENDPOINT_GUARD_ENABLED: bool = True
    ENDPOINT_GUARD_SOURCE: EndpointGuardSource = "settings"
    ENDPOINT_GUARD_RULES: str = "[]"
    ENDPOINT_GUARD_REDIS_KEY: str = "endpoint_guard:rules"
    ENDPOINT_GUARD_CACHE_TTL_SECONDS: int = 10
    ENDPOINT_GUARD_FAIL_OPEN: bool = True

    model_config = SettingsConfigDict(
        case_sensitive=True,
        extra="ignore",
        env_file_encoding="utf-8",
    )

    @field_validator("DB_TYPE", mode="before")
    def validate_db_type(cls, v: str) -> str:
        """校验数据库类型"""
        if not v:
            raise ValueError("DB_TYPE is required and cannot be empty")
        allowed = list(DB_DRIVER_MAP.keys())
        if v not in allowed:
            raise ValueError(f"DB_TYPE must be one of {allowed}, got '{v}'")
        return v

    @model_validator(mode="after")
    def decrypt_sensitive_fields(self) -> "Settings":
        """解密敏感字段"""
        fields_to_decrypt = ["DB_PASSWORD", "DB_READ_PASSWORD", "REDIS_PASSWORD"]
        aes_key = self.AES_SECRET.get_secret_value()

        if not aes_key:
            return self

        for field in fields_to_decrypt:
            secret_value: SecretStr | None = getattr(self, field)
            if secret_value is None:
                continue
            original_value = secret_value.get_secret_value()
            if original_value.startswith("ENC(") and original_value.endswith(")"):
                try:
                    decrypted_value = aes_decrypt(original_value[4:-1], aes_key)
                    object.__setattr__(self, field, SecretStr(decrypted_value))
                except Exception as e:
                    logger.error(f"Failed to decrypt field '{field}': {str(e)}")
                    raise ValueError(f"Failed to decrypt field '{field}'") from e
        return self

    @property
    def sqlalchemy_database_uri(self) -> str:
        """根据数据库类型动态生成连接 URI"""
        driver = DB_DRIVER_MAP.get(self.DB_TYPE)
        if not driver:
            raise ValueError(f"Unsupported database type: {self.DB_TYPE}")

        password = self.DB_PASSWORD.get_secret_value()

        if self.DB_TYPE == "mysql":
            return str(
                MySQLDsn.build(
                    scheme=driver,
                    username=self.DB_USERNAME,
                    password=password,
                    host=self.DB_HOST,
                    port=self.DB_PORT,
                    path=self.DB_DATABASE,
                    query="charset=utf8mb4",
                )
            )
        elif self.DB_TYPE == "postgresql":
            return str(
                PostgresDsn.build(
                    scheme=driver,
                    username=self.DB_USERNAME,
                    password=password,
                    host=self.DB_HOST,
                    port=self.DB_PORT,
                    path=self.DB_DATABASE,
                )
            )
        elif self.DB_TYPE == "oracle":
            # Oracle 连接格式: oracle+oracledb://user:pass@host:port/?service_name=xxx
            if password:
                from urllib.parse import quote_plus

                password = quote_plus(password)
                return f"{driver}://{self.DB_USERNAME}:{password}@{self.DB_HOST}:{self.DB_PORT}/?service_name={self.DB_SERVICE_NAME}"
            return f"{driver}://{self.DB_USERNAME}@{self.DB_HOST}:{self.DB_PORT}/?service_name={self.DB_SERVICE_NAME}"
        else:
            raise ValueError(f"Unsupported database type: {self.DB_TYPE}")

    @property
    def sqlalchemy_read_database_uri(self) -> str | None:
        """
        根据数据库类型动态生成只读副本的连接 URI。
        未配置的字段自动 fallback 到主库同名字段。
        如果 DB_READ_HOST 未设置，返回 None 表示不启用读写分离。
        """
        if self.DB_READ_HOST is None:
            return None

        driver = DB_DRIVER_MAP.get(self.DB_TYPE)
        if not driver:
            raise ValueError(f"Unsupported database type: {self.DB_TYPE}")

        # 未设置的字段 fallback 到主库
        host = self.DB_READ_HOST
        port = self.DB_READ_PORT or self.DB_PORT
        username = self.DB_READ_USERNAME or self.DB_USERNAME
        password = (
            self.DB_READ_PASSWORD.get_secret_value()
            if self.DB_READ_PASSWORD is not None
            else self.DB_PASSWORD.get_secret_value()
        )
        database = self.DB_READ_DATABASE or self.DB_DATABASE
        service_name = self.DB_READ_SERVICE_NAME or self.DB_SERVICE_NAME

        if self.DB_TYPE == "mysql":
            return str(
                MySQLDsn.build(
                    scheme=driver,
                    username=username,
                    password=password,
                    host=host,
                    port=port,
                    path=database,
                    query="charset=utf8mb4",
                )
            )
        elif self.DB_TYPE == "postgresql":
            return str(
                PostgresDsn.build(
                    scheme=driver,
                    username=username,
                    password=password,
                    host=host,
                    port=port,
                    path=database,
                )
            )
        elif self.DB_TYPE == "oracle":
            if password:
                from urllib.parse import quote_plus

                password = quote_plus(password)
                return f"{driver}://{username}:{password}@{host}:{port}/?service_name={service_name}"
            return f"{driver}://{username}@{host}:{port}/?service_name={service_name}"
        else:
            raise ValueError(f"Unsupported database type: {self.DB_TYPE}")

    @property
    def redis_url(self) -> str:
        password = self.REDIS_PASSWORD.get_secret_value()
        return str(
            RedisDsn.build(
                scheme="redis",
                username=None,
                password=password if password else None,
                host=self.REDIS_HOST,
                port=self.REDIS_PORT,
                path=f"{self.REDIS_DB}",
            )
        )


# =========================================================
# 配置加载器
# =========================================================


def _setup_startup_logger():
    """配置启动日志"""
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    logger.add(
        log_dir / "startup.log",
        rotation="1 day",
        retention="7 days",
        level="INFO",
        enqueue=True,
        encoding="utf-8",
    )
    return logger


def detect_app_env() -> str:
    """
    检测应用环境

    规则：
    - 只从 .secrets 文件读取 APP_ENV
    - 如果文件不存在或 APP_ENV 未设置，则报错终止
    """
    secrets_path = BASE_DIR / "configs" / ".secrets"

    # 检查文件是否存在
    if not secrets_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {secrets_path}")

    # 从文件读取配置
    secrets_dict = dotenv_values(secrets_path)
    app_env = secrets_dict.get("APP_ENV")

    if not app_env:
        raise ValueError(f"APP_ENV not found in {secrets_path}")

    return app_env


def _validate_secrets_file() -> tuple[Path, dict]:
    """
    验证 .secrets 文件存在并返回路径和内容

    Returns:
        tuple[Path, dict]: (文件路径, 配置字典)

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: APP_ENV 未设置
    """
    secrets_path = BASE_DIR / "configs" / ".secrets"

    # 检查文件是否存在
    if not secrets_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {secrets_path}")

    # 读取配置
    secrets_dict = dotenv_values(secrets_path)

    # 验证必需的 APP_ENV
    app_env = secrets_dict.get("APP_ENV")
    if not app_env:
        raise ValueError(f"APP_ENV not found in {secrets_path}")

    return secrets_path, secrets_dict


def load_config() -> Settings:
    """
    加载应用配置

    加载顺序：
    1. 验证 .secrets 文件并获取应用环境
    2. 检查对应环境配置文件是否存在
    3. 加载配置文件 (.env.{env} 和 .secrets)
    """
    _logger = _setup_startup_logger()
    _logger.info("Loading configuration...")

    # 1. 验证 .secrets 文件并获取应用环境
    try:
        secrets_path, secrets_dict = _validate_secrets_file()
        app_env = secrets_dict["APP_ENV"]  # 已经验证过存在
        _logger.info(f"Detected Environment: {app_env}")
    except (FileNotFoundError, ValueError) as e:
        _logger.critical(f"Configuration validation failed: {e}")
        raise

    # 2. 检查对应环境文件是否存在
    env_file_path = BASE_DIR / "configs" / f".env.{app_env}"
    if not env_file_path.exists():
        msg = f"CRITICAL: Config file missing for environment '{app_env}' at {env_file_path}"
        _logger.critical(msg)
        raise FileNotFoundError(msg)

    # 4. 加载配置
    # load_files 顺序：[.env.dev, .secrets] -> 后者覆盖前者
    load_files = [env_file_path, secrets_path]
    _logger.info(f"Loading files: {[f.name for f in load_files]}")

    try:
        _settings = Settings(_env_file=load_files)  # type: ignore
        _logger.success("Configuration loaded successfully.")

        # 根据 ECHO_CONFIG 决定是否打印配置
        if _settings.ECHO_CONFIG:
            _logger.info("=" * 50)
            _logger.info("Configuration Details (ECHO_CONFIG=true):")
            for key, value in _settings.model_dump().items():
                # SecretStr 类型需要获取原始值
                if hasattr(_settings, key) and hasattr(
                    getattr(_settings, key), "get_secret_value"
                ):
                    value = getattr(_settings, key).get_secret_value()
                _logger.info(f"  {key}: {value}")

            _logger.info("=" * 50)

        return _settings
    except Exception as e:
        _logger.critical(f"Config load failed: {e}")
        raise


# 全局配置实例（私有）
_settings_instance: Settings | None = None


def get_settings() -> Settings:
    """
    获取配置实例（线程安全的单例模式）
    """
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = load_config()
    return _settings_instance


def init_settings() -> Settings:
    """
    初始化并返回配置实例
    在应用启动时调用此函数
    """
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = load_config()
    return _settings_instance


def reset_settings():
    """
    重置配置实例（主要用于测试）
    """
    global _settings_instance
    _settings_instance = None


# 使用 lazy_proxy 创建延迟加载的配置实例
settings = lazy_proxy(get_settings)

__all__ = ["settings", "init_settings", "get_settings", "reset_settings", "Settings"]
