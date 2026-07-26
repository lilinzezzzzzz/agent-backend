"""应用配置模块

包含配置类定义、加载逻辑和全局配置实例
"""

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

from dotenv import dotenv_values
from loguru import logger
from pydantic import (
    AnyHttpUrl,
    MySQLDsn,
    PositiveInt,
    PostgresDsn,
    RedisDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from internal import BASE_DIR
from pkg.crypter.aes import aes_decrypt
from pkg.lazy_proxy import lazy_proxy
from pkg.logger import LogFormat
from pkg.toolkit.string import mask_string

# =========================================================
# 配置定义
# =========================================================

# 支持的数据库类型
DBType = Literal["mysql", "postgresql", "oracle"]
EndpointGuardSource = Literal["settings", "redis", "settings+redis"]
EmbeddingProvider = Literal["openai_compatible"]
AppEnv = Literal["local", "dev", "test", "prod"]
APP_ENV_VALUES = {"local", "dev", "test", "prod"}

# 数据库驱动映射
DB_DRIVER_MAP: dict[str, str] = {
    "mysql": "mysql+aiomysql",
    "postgresql": "postgresql+asyncpg",
    "oracle": "oracle+oracledb",
}

SENSITIVE_CONFIG_KEYWORDS = (
    "SECRET",
    "PASSWORD",
    "TOKEN",
    "API_KEY",
    "PRIVATE_KEY",
    "ACCESS_KEY",
    "CREDENTIAL",
)
NON_SENSITIVE_CONFIG_KEYS = {
    "CONFIG_ECHO_REVEAL_SECRETS",
}
SECRET_FILE_KEYS = frozenset(
    {
        "AES_SECRET",
        "DB_PASSWORD",
        "DB_READ_PASSWORD",
        "EMBEDDING_API_KEY",
        "JWT_SECRET",
        "LLM_DEEPSEEK_API_KEY",
        "LLM_MIMO_API_KEY",
        "REDIS_PASSWORD",
        "WECHAT_APP_ID",
        "WECHAT_APP_SECRET",
    }
)
CONFIG_ECHO_MIN_PARTIAL_SECRET_LENGTH = 8


class Settings(BaseSettings):
    """
    应用全局配置。
    """

    # --- 核心环境配置 ---
    APP_ENV: AppEnv
    DEBUG: bool

    # --- 日志配置 ---
    LOG_FORMAT: LogFormat  # 日志格式: TEXT 或 JSON
    LOG_BASE_DIR: Path | None = None
    LOG_WRITE_TO_FILE: bool
    LOG_WRITE_TO_CONSOLE: bool

    # --- OpenTelemetry ---
    OTEL_TRACING_ENABLED: bool
    OTEL_SERVICE_NAME: str
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: AnyHttpUrl | None = None
    OTEL_EXPORTER_OTLP_TIMEOUT_SECONDS: PositiveInt

    # --- 密钥配置 ---
    AES_SECRET: SecretStr
    JWT_SECRET: SecretStr
    JWT_ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_SECONDS: int
    CONFIG_ECHO_REVEAL_SECRETS: bool  # 打印配置时是否显示敏感值

    # --- CORS ---
    BACKEND_CORS_ORIGINS: list[str]

    # --- 第三方登录 ---
    WECHAT_APP_ID: str
    WECHAT_APP_SECRET: SecretStr
    WECHAT_GRANT_TYPE: str

    # --- LLM ---
    LLM_DEFAULT_PROVIDER: str
    LLM_MIMO_BASE_URL: str
    LLM_MIMO_MODEL: str
    LLM_MIMO_API_KEY: SecretStr
    LLM_DEEPSEEK_BASE_URL: str
    LLM_DEEPSEEK_MODEL: str
    LLM_DEEPSEEK_API_KEY: SecretStr

    # --- Embedding ---
    EMBEDDING_PROVIDER: EmbeddingProvider
    EMBEDDING_BASE_URL: str
    EMBEDDING_MODEL: str
    EMBEDDING_API_KEY: SecretStr
    EMBEDDING_DIMENSION: PositiveInt
    EMBEDDING_TIMEOUT_SECONDS: PositiveInt

    # --- Vector store ---
    MILVUS_URI: AnyHttpUrl
    MILVUS_DB_NAME: str
    MILVUS_TIMEOUT_SECONDS: PositiveInt

    # --- Agent ---
    AGENT_ACTION_CONFIRMATION_SECONDS: PositiveInt
    AGENT_ACTION_IDEMPOTENCY_SECONDS: PositiveInt

    # --- RAG ---
    RAG_ALLOWED_DOMAINS: list[str]
    RAG_ALLOWED_KB_IDS: list[int]
    RAG_MAX_CONTEXT_CHARS: PositiveInt

    # --- Database ---
    DB_TYPE: DBType  # 数据库类型: mysql, postgresql, oracle (必填)
    DB_HOST: str
    DB_PORT: int
    DB_USERNAME: str
    DB_PASSWORD: SecretStr
    DB_DATABASE: str
    DB_SERVICE_NAME: str | None = None  # Oracle 专用: Service Name
    DB_ECHO: bool  # 是否输出 SQL 日志

    # --- Database Read Replica (可选，不配置则不启用读写分离) ---
    DB_READ_HOST: str | None = None
    DB_READ_PORT: int | None = None
    DB_READ_USERNAME: str | None = None
    DB_READ_PASSWORD: SecretStr | None = None
    DB_READ_DATABASE: str | None = None
    DB_READ_SERVICE_NAME: str | None = None  # Oracle 专用

    # --- Redis ---
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_PASSWORD: SecretStr
    REDIS_DB: int

    # --- Celery PostgreSQL idempotency ---
    CELERY_IDEMPOTENCY_ENABLED: bool
    CELERY_EXECUTION_DEADLINE_GRACE_SECONDS: PositiveInt
    CELERY_QUEUE_START_TIMEOUT_SECONDS: PositiveInt
    CELERY_PUBLISH_CONFIRM_TIMEOUT_SECONDS: PositiveInt
    CELERY_PUBLISH_RECONCILER_INTERVAL_SECONDS: PositiveInt
    CELERY_ORPHAN_FENCE_SECONDS: PositiveInt
    CELERY_RECONCILER_BEAT_ENABLED: bool
    CELERY_RECONCILER_INTERVAL_SECONDS: PositiveInt
    CELERY_RECONCILER_BATCH_SIZE: PositiveInt
    CELERY_RECONCILER_QUEUE: str
    CELERY_TASK_MAX_PAYLOAD_BYTES: PositiveInt

    # --- Endpoint Guard ---
    ENDPOINT_GUARD_ENABLED: bool
    ENDPOINT_GUARD_SOURCE: EndpointGuardSource
    ENDPOINT_GUARD_RULES: str
    ENDPOINT_GUARD_REDIS_KEY: str
    ENDPOINT_GUARD_CACHE_TTL_SECONDS: int
    ENDPOINT_GUARD_FAIL_OPEN: bool

    model_config = SettingsConfigDict(
        case_sensitive=True,
        extra="forbid",
        env_file_encoding="utf-8",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        _settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Load application settings only from explicit arguments and files."""
        del env_settings
        return init_settings, dotenv_settings, file_secret_settings

    @field_validator("DB_TYPE", mode="before")
    def validate_db_type(cls, v: str) -> str:
        """校验数据库类型"""
        if not v:
            raise ValueError("DB_TYPE is required and cannot be empty")
        allowed = list(DB_DRIVER_MAP.keys())
        if v not in allowed:
            raise ValueError(f"DB_TYPE must be one of {allowed}, got '{v}'")
        return v

    @field_validator("MILVUS_DB_NAME")
    def validate_milvus_db_name(cls, value: str) -> str:
        """Milvus database 名称必须是非空字符串。"""
        normalized = value.strip()
        if not normalized:
            raise ValueError("MILVUS_DB_NAME cannot be empty")
        return normalized

    @field_validator("LOG_BASE_DIR")
    def resolve_log_base_dir(cls, value: Path | None) -> Path | None:
        """相对日志目录统一基于项目根目录解析。"""
        if value is None or value.is_absolute():
            return value
        return (BASE_DIR / value).resolve()

    @model_validator(mode="after")
    def validate_log_output(self) -> "Settings":
        """校验日志至少有一个输出目标，且文件输出必须配置目录。"""
        if not self.LOG_WRITE_TO_FILE and not self.LOG_WRITE_TO_CONSOLE:
            raise ValueError(
                "LOG_WRITE_TO_FILE and LOG_WRITE_TO_CONSOLE cannot both be false"
            )
        if self.LOG_WRITE_TO_FILE and self.LOG_BASE_DIR is None:
            raise ValueError("LOG_BASE_DIR is required when LOG_WRITE_TO_FILE=true")
        return self

    @model_validator(mode="after")
    def validate_oracle_service_name(self) -> "Settings":
        """Oracle 主库必须显式配置 service name。"""
        if self.DB_TYPE == "oracle" and not self.DB_SERVICE_NAME:
            raise ValueError("DB_SERVICE_NAME is required when DB_TYPE=oracle")
        return self

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

    @model_validator(mode="after")
    def validate_tracing_exporter(self) -> "Settings":
        """启用 tracing 时必须配置真实的 OTLP/HTTP Trace 接收端。"""
        if (
            self.OTEL_TRACING_ENABLED
            and self.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT is None
        ):
            raise ValueError(
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT is required when "
                "OTEL_TRACING_ENABLED=true"
            )
        return self

    @model_validator(mode="after")
    def validate_celery_idempotency(self) -> "Settings":
        """校验 Celery PostgreSQL 幂等状态机的安全边界。"""
        if not self.CELERY_IDEMPOTENCY_ENABLED:
            return self
        if self.DB_TYPE != "postgresql":
            raise ValueError("CELERY_IDEMPOTENCY_ENABLED requires DB_TYPE=postgresql")
        if self.CELERY_EXECUTION_DEADLINE_GRACE_SECONDS > 300:
            raise ValueError(
                "CELERY_EXECUTION_DEADLINE_GRACE_SECONDS cannot exceed 300"
            )
        if (
            self.CELERY_QUEUE_START_TIMEOUT_SECONDS
            <= self.CELERY_RECONCILER_INTERVAL_SECONDS
        ):
            raise ValueError(
                "CELERY_QUEUE_START_TIMEOUT_SECONDS must exceed "
                "CELERY_RECONCILER_INTERVAL_SECONDS"
            )
        if (
            self.CELERY_PUBLISH_CONFIRM_TIMEOUT_SECONDS
            <= self.CELERY_PUBLISH_RECONCILER_INTERVAL_SECONDS
        ):
            raise ValueError(
                "CELERY_PUBLISH_CONFIRM_TIMEOUT_SECONDS must exceed "
                "CELERY_PUBLISH_RECONCILER_INTERVAL_SECONDS"
            )
        if not self.CELERY_RECONCILER_QUEUE.strip():
            raise ValueError("CELERY_RECONCILER_QUEUE cannot be empty")
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
    """配置启动阶段的同步 stderr 日志，直到正式 Logger 接管。"""
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        enqueue=False,
        colorize=False,
        diagnose=False,
    )
    return logger


def detect_app_env() -> AppEnv:
    """
    检测应用环境

    规则：
    - Compose/容器环境从进程环境变量读取 APP_ENV
    - 本地直接运行时可从仓库根目录 .env 读取 APP_ENV
    - 两处同时存在但值不一致，或值不受支持时，报错终止
    """
    root_env_path = BASE_DIR / ".env"
    root_env = (
        dotenv_values(root_env_path).get("APP_ENV") if root_env_path.exists() else None
    )
    process_env = os.getenv("APP_ENV")

    if process_env and root_env and process_env != root_env:
        raise ValueError(
            f"APP_ENV conflict: process environment is '{process_env}' but {root_env_path} contains '{root_env}'"
        )

    app_env = process_env or root_env
    if not app_env:
        raise ValueError(
            f"APP_ENV must be set in the process environment or {root_env_path}"
        )
    if app_env not in APP_ENV_VALUES:
        supported = ", ".join(sorted(APP_ENV_VALUES))
        raise ValueError(
            f"Unsupported APP_ENV '{app_env}'; expected one of: {supported}"
        )

    return cast(AppEnv, app_env)


def _required_config_keys() -> set[str]:
    """返回必须由环境配置文件显式提供的字段。"""
    return {
        name
        for name, field in Settings.model_fields.items()
        if field.is_required() and name not in SECRET_FILE_KEYS and name != "APP_ENV"
    }


def _required_secret_keys() -> set[str]:
    """返回必须由密钥文件显式提供的字段。"""
    return {
        name
        for name, field in Settings.model_fields.items()
        if field.is_required() and name in SECRET_FILE_KEYS
    }


def _validate_environment_file(env_file_path: Path) -> dict[str, str | None]:
    """验证环境配置文件的字段来源和完整性。"""
    env_values = dict(dotenv_values(env_file_path))
    misplaced_secret_keys = sorted(set(env_values) & SECRET_FILE_KEYS)
    if misplaced_secret_keys:
        keys = ", ".join(misplaced_secret_keys)
        raise ValueError(
            f"Secret keys must be moved from {env_file_path} to .secrets: {keys}"
        )

    missing_keys = sorted(_required_config_keys() - set(env_values))
    if missing_keys:
        keys = ", ".join(missing_keys)
        raise ValueError(
            f"Required configuration keys missing from {env_file_path}: {keys}"
        )

    return env_values


def _validate_secrets_file() -> tuple[Path, dict[str, str | None]]:
    """
    验证 .secrets 文件存在并返回路径和内容

    Returns:
        tuple[Path, dict]: (文件路径, 配置字典)

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: .secrets 包含非密钥字段或缺少必填密钥
    """
    secrets_path = BASE_DIR / ".secrets"

    # 检查文件是否存在
    if not secrets_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {secrets_path}")

    # 读取配置
    secrets_dict = dotenv_values(secrets_path)

    unsupported_keys = sorted(set(secrets_dict) - SECRET_FILE_KEYS)
    if unsupported_keys:
        keys = ", ".join(unsupported_keys)
        raise ValueError(
            f"Non-secret or unsupported keys found in {secrets_path}: {keys}"
        )

    missing_keys = sorted(_required_secret_keys() - set(secrets_dict))
    if missing_keys:
        keys = ", ".join(missing_keys)
        raise ValueError(f"Required secret keys missing from {secrets_path}: {keys}")

    return secrets_path, secrets_dict


def _sensitive_secret_config_keys(secrets: Mapping[str, str | None]) -> set[str]:
    """Return sensitive config keys declared in the repository-root .secrets."""
    return {
        key
        for key, value in secrets.items()
        if value is not None
        and key not in NON_SENSITIVE_CONFIG_KEYS
        and any(keyword in key.upper() for keyword in SENSITIVE_CONFIG_KEYWORDS)
    }


def _config_echo_value(
    settings: Settings,
    key: str,
    value: Any,
    *,
    sensitive_keys: set[str],
    reveal_sensitive: bool,
) -> Any:
    setting_value = getattr(settings, key, value)
    if hasattr(setting_value, "get_secret_value"):
        value = setting_value.get_secret_value()

    if reveal_sensitive or key not in sensitive_keys:
        return value

    if value is None:
        return None
    if isinstance(value, str):
        if not value:
            return "<empty>"
        if len(value) <= CONFIG_ECHO_MIN_PARTIAL_SECRET_LENGTH:
            return f"*** (len={len(value)})"
        return f"{mask_string(value, show_prefix=4, show_suffix=4)} (len={len(value)})"
    return "***"


def _echo_loaded_config(
    settings: Settings,
    secrets: Mapping[str, str | None],
) -> None:
    sensitive_keys = _sensitive_secret_config_keys(secrets)
    reveal_sensitive = settings.CONFIG_ECHO_REVEAL_SECRETS
    echo_mode = "unmasked" if reveal_sensitive else "masked"

    logger.info("=" * 50)
    logger.success(
        f"Configuration Details (CONFIG_ECHO_REVEAL_SECRETS={settings.CONFIG_ECHO_REVEAL_SECRETS}, {echo_mode}):"
    )
    for key, value in settings.model_dump().items():
        echo_value = _config_echo_value(
            settings,
            key,
            value,
            sensitive_keys=sensitive_keys,
            reveal_sensitive=reveal_sensitive,
        )
        logger.success(f"  {key}: {echo_value}")

    logger.info("=" * 50)


def load_config() -> Settings:
    """
    加载应用配置

    加载顺序：
    1. 从进程环境或仓库根目录 .env 获取 APP_ENV
    2. 验证 .secrets 文件不再包含 APP_ENV
    3. 验证对应环境配置文件的字段来源和完整性
    4. 加载配置文件 (.env.{env} 和 .secrets)，并显式注入 APP_ENV
    """
    _logger = _setup_startup_logger()
    _logger.info("Loading configuration...")

    # 1-2. 获取唯一 APP_ENV，并验证密钥文件
    try:
        app_env = detect_app_env()
        secrets_path, secrets_dict = _validate_secrets_file()
        _logger.info(f"Detected Environment: {app_env}")
    except (FileNotFoundError, ValueError) as e:
        _logger.critical(f"Configuration validation failed: {e}")
        raise

    # 3. 检查对应环境文件是否存在
    env_file_path = BASE_DIR / "configs" / f".env.{app_env}"
    if not env_file_path.exists():
        msg = f"CRITICAL: Config file missing for environment '{app_env}' at {env_file_path}"
        _logger.critical(msg)
        raise FileNotFoundError(msg)

    try:
        _validate_environment_file(env_file_path)
    except ValueError as e:
        _logger.critical(f"Configuration validation failed: {e}")
        raise

    # 4. 加载已完成字段隔离校验的配置和密钥文件；不读取其他 shell 环境变量
    load_files = [env_file_path, secrets_path]
    _logger.info(f"Loading files: {[f.name for f in load_files]}")

    try:
        _settings = Settings(APP_ENV=app_env, _env_file=load_files)  # type: ignore
        _logger.success("Configuration loaded successfully.")

        _echo_loaded_config(_settings, secrets_dict)

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
