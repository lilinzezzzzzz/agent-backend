"""应用配置模块

包含配置类定义、加载逻辑和全局配置实例
"""

import os
import sys
import tomllib
from collections.abc import Generator, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cache
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import quote, urlunsplit

from dotenv import dotenv_values
from loguru import logger
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)
from sqlalchemy.engine import URL

from internal import BASE_DIR
from pkg.crypter.aes import aes_decrypt
from pkg.lazy_proxy import lazy_proxy
from pkg.logger import LogFormat
from pkg.toolkit.string import mask_string

# =========================================================
# 配置定义
# =========================================================

# 支持的数据库类型
DBType = Literal["mysql", "postgresql"]
EndpointGuardSource = Literal["settings", "redis", "settings+redis"]
EmbeddingProvider = Literal["openai_compatible"]
AppEnv = Literal["local", "dev", "test", "prod"]
APP_ENV_VALUES = {"local", "dev", "test", "prod"}
_CURRENT_APP_CONFIG_PATH: ContextVar[Path | None] = ContextVar(
    "current_app_config_path",
    default=None,
)


class _AppTomlSettingsSource(TomlConfigSettingsSource):
    """按 load_config 选择的应用 TOML 文件加载配置。"""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(
            settings_cls,
            toml_file=_CURRENT_APP_CONFIG_PATH.get(),
        )


@contextmanager
def _use_app_config_path(path: Path) -> Generator[None]:
    token = _CURRENT_APP_CONFIG_PATH.set(path)
    try:
        yield
    finally:
        _CURRENT_APP_CONFIG_PATH.reset(token)


# 数据库驱动映射
DB_DRIVER_MAP: dict[str, str] = {
    "mysql": "mysql+aiomysql",
    "postgresql": "postgresql+asyncpg",
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


def _build_redis_url(*, host: str, port: int, password: str, db: int) -> str:
    """构造可供 redis-py 和 Celery 使用的 Redis URL。"""
    url_host = host if ":" not in host or host.startswith("[") else f"[{host}]"
    credentials = f":{quote(password, safe='')}@" if password else ""
    netloc = f"{credentials}{url_host}:{port}"
    return urlunsplit(("redis", netloc, f"/{db}", "", ""))


class _ConfigModel(BaseModel):
    """应用配置分组模型，拒绝未知字段。"""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class LogSettings(_ConfigModel):
    format: LogFormat
    base_dir: Path | None = None
    write_to_file: bool
    write_to_console: bool

    @field_validator("base_dir")
    def resolve_base_dir(cls, value: Path | None) -> Path | None:
        """相对日志目录统一基于项目根目录解析。"""
        if value is None or value.is_absolute():
            return value
        return (BASE_DIR / value).resolve()

    @model_validator(mode="after")
    def validate_output(self) -> "LogSettings":
        """校验日志至少有一个输出目标，且文件输出必须配置目录。"""
        if not self.write_to_file and not self.write_to_console:
            raise ValueError(
                "LOG_WRITE_TO_FILE and LOG_WRITE_TO_CONSOLE cannot both be false"
            )
        if self.write_to_file and self.base_dir is None:
            raise ValueError("LOG_BASE_DIR is required when LOG_WRITE_TO_FILE=true")
        return self


class OtelSettings(_ConfigModel):
    tracing_enabled: bool
    operation_spans_enabled: bool
    metrics_enabled: bool
    logs_enabled: bool
    auto_configure: bool
    service_name: str
    exporter_otlp_traces_endpoint: AnyHttpUrl | None = None
    exporter_otlp_timeout_seconds: PositiveInt

    @field_validator("auto_configure")
    def validate_auto_configure(cls, value: bool) -> bool:
        """当前由 Settings 管理 exporter，禁止框架另从进程环境自动配置。"""
        if value:
            raise ValueError(
                "OTEL_AUTO_CONFIGURE must be false: providers and exporters are "
                "managed by application Settings"
            )
        return value

    @model_validator(mode="after")
    def validate_tracing_exporter(self) -> "OtelSettings":
        """启用 tracing 时必须配置真实的 OTLP/HTTP Trace 接收端。"""
        if self.tracing_enabled and self.exporter_otlp_traces_endpoint is None:
            raise ValueError(
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT is required when "
                "OTEL_TRACING_ENABLED=true"
            )
        return self


class JwtSettings(_ConfigModel):
    algorithm: str


class AccessTokenSettings(_ConfigModel):
    expire_seconds: int


class AuthSettings(_ConfigModel):
    jwt: JwtSettings
    access_token: AccessTokenSettings


class CorsSettings(_ConfigModel):
    origins: list[str]


class WechatSettings(_ConfigModel):
    grant_type: str


class LlmProviderSettings(_ConfigModel):
    base_url: str
    model: str


class LlmSettings(_ConfigModel):
    default_provider: str
    mimo: LlmProviderSettings
    deepseek: LlmProviderSettings


class EmbeddingSettings(_ConfigModel):
    provider: EmbeddingProvider
    base_url: str
    model: str
    dimension: PositiveInt
    timeout_seconds: PositiveInt


class MilvusSettings(_ConfigModel):
    uri: AnyHttpUrl
    db_name: str
    timeout_seconds: PositiveInt

    @field_validator("db_name")
    def validate_db_name(cls, value: str) -> str:
        """Milvus database 名称必须是非空字符串。"""
        normalized = value.strip()
        if not normalized:
            raise ValueError("MILVUS_DB_NAME cannot be empty")
        return normalized


class AgentSettings(_ConfigModel):
    action_confirmation_seconds: PositiveInt
    action_idempotency_seconds: PositiveInt
    lease_seconds: PositiveInt
    control_poll_seconds: PositiveInt
    cancel_grace_seconds: PositiveInt
    stream_buffer_size: PositiveInt
    checkpoint_max_bytes: PositiveInt
    llm_timeout_seconds: PositiveInt

    @model_validator(mode="after")
    def validate_managed_run(self) -> "AgentSettings":
        """校验受管理 Agent 运行的心跳、取消收尾与 checkpoint 上限。"""
        if self.control_poll_seconds >= self.lease_seconds:
            raise ValueError(
                "AGENT_CONTROL_POLL_SECONDS must be less than AGENT_LEASE_SECONDS"
            )
        if self.cancel_grace_seconds <= self.llm_timeout_seconds:
            raise ValueError(
                "AGENT_CANCEL_GRACE_SECONDS must cover AGENT_LLM_TIMEOUT_SECONDS "
                "plus the checkpoint commit budget"
            )
        if self.cancel_grace_seconds > 300:
            raise ValueError("AGENT_CANCEL_GRACE_SECONDS cannot exceed 300")
        if self.checkpoint_max_bytes > 16 * 1024 * 1024:
            raise ValueError("AGENT_CHECKPOINT_MAX_BYTES cannot exceed 16 MiB")
        return self


class RagSettings(_ConfigModel):
    allowed_domains: list[str]
    allowed_kb_ids: list[int]
    max_context_chars: PositiveInt


class DatabaseReadReplicaSettings(_ConfigModel):
    host: str
    port: int | None = None
    username: str | None = None
    database: str | None = None


class DatabaseSettings(_ConfigModel):
    type: DBType
    host: str
    port: int
    username: str
    database: str
    echo: bool
    read_replica: DatabaseReadReplicaSettings | None = None

    @field_validator("type", mode="before")
    def validate_db_type(cls, value: str) -> str:
        """校验数据库类型。"""
        if not value:
            raise ValueError("DB_TYPE is required and cannot be empty")
        allowed = list(DB_DRIVER_MAP.keys())
        if value not in allowed:
            raise ValueError(f"DB_TYPE must be one of {allowed}")
        return value


class RedisSettings(_ConfigModel):
    host: str
    port: int
    db: int


class CelerySettings(_ConfigModel):
    idempotency_enabled: bool
    execution_deadline_grace_seconds: PositiveInt
    queue_start_timeout_seconds: PositiveInt
    publish_confirm_timeout_seconds: PositiveInt
    publish_reconciler_interval_seconds: PositiveInt
    orphan_fence_seconds: PositiveInt
    reconciler_beat_enabled: bool
    reconciler_interval_seconds: PositiveInt
    reconciler_batch_size: PositiveInt
    reconciler_queue: str
    task_max_payload_bytes: PositiveInt

    @model_validator(mode="after")
    def validate_idempotency_timing(self) -> "CelerySettings":
        """校验 Celery 幂等状态机的内部时序边界。"""
        if not self.idempotency_enabled:
            return self
        if self.execution_deadline_grace_seconds > 300:
            raise ValueError(
                "CELERY_EXECUTION_DEADLINE_GRACE_SECONDS cannot exceed 300"
            )
        if self.queue_start_timeout_seconds <= self.reconciler_interval_seconds:
            raise ValueError(
                "CELERY_QUEUE_START_TIMEOUT_SECONDS must exceed "
                "CELERY_RECONCILER_INTERVAL_SECONDS"
            )
        if self.publish_confirm_timeout_seconds <= (
            self.publish_reconciler_interval_seconds
        ):
            raise ValueError(
                "CELERY_PUBLISH_CONFIRM_TIMEOUT_SECONDS must exceed "
                "CELERY_PUBLISH_RECONCILER_INTERVAL_SECONDS"
            )
        if not self.reconciler_queue.strip():
            raise ValueError("CELERY_RECONCILER_QUEUE cannot be empty")
        return self


class EndpointGuardSettings(_ConfigModel):
    enabled: bool
    source: EndpointGuardSource
    rules: list[dict[str, object]]
    redis_key: str
    cache_ttl_seconds: int
    fail_open: bool


class ConfigEchoSettings(_ConfigModel):
    reveal_secrets: bool


class SecretsSettings(_ConfigModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    aes_secret: SecretStr = Field(validation_alias="AES_SECRET")
    jwt_secret: SecretStr = Field(validation_alias="JWT_SECRET")
    db_password: SecretStr = Field(validation_alias="DB_PASSWORD")
    db_read_password: SecretStr | None = Field(
        default=None,
        validation_alias="DB_READ_PASSWORD",
    )
    redis_password: SecretStr = Field(validation_alias="REDIS_PASSWORD")
    wechat_app_id: str = Field(validation_alias="WECHAT_APP_ID")
    wechat_app_secret: SecretStr = Field(validation_alias="WECHAT_APP_SECRET")
    llm_mimo_api_key: SecretStr = Field(validation_alias="LLM_MIMO_API_KEY")
    llm_deepseek_api_key: SecretStr = Field(validation_alias="LLM_DEEPSEEK_API_KEY")
    embedding_api_key: SecretStr = Field(validation_alias="EMBEDDING_API_KEY")


class Settings(BaseSettings):
    """
    应用全局配置。
    """

    app_env: AppEnv
    debug: bool

    log: LogSettings
    otel: OtelSettings
    auth: AuthSettings
    cors: CorsSettings
    wechat: WechatSettings
    llm: LlmSettings
    embedding: EmbeddingSettings
    milvus: MilvusSettings
    agent: AgentSettings
    rag: RagSettings
    db: DatabaseSettings
    redis: RedisSettings
    celery: CelerySettings
    endpoint_guard: EndpointGuardSettings
    config_echo: ConfigEchoSettings
    secrets: SecretsSettings

    model_config = SettingsConfigDict(
        case_sensitive=True,
        extra="forbid",
        hide_input_in_errors=True,
        env_file_encoding="utf-8",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Load TOML application settings from the selected environment file."""
        del env_settings, dotenv_settings, file_secret_settings
        return init_settings, _AppTomlSettingsSource(settings_cls)

    @model_validator(mode="after")
    def decrypt_sensitive_fields(self) -> "Settings":
        """解密敏感字段。"""
        aes_key = self.secrets.aes_secret.get_secret_value()

        if not aes_key:
            return self

        for field in ("db_password", "db_read_password", "redis_password"):
            secret_value: SecretStr | None = getattr(self.secrets, field)
            if secret_value is None:
                continue
            original_value = secret_value.get_secret_value()
            if original_value.startswith("ENC(") and original_value.endswith(")"):
                try:
                    decrypted_value = aes_decrypt(original_value[4:-1], aes_key)
                    object.__setattr__(self.secrets, field, SecretStr(decrypted_value))
                except Exception as e:
                    logger.error(f"Failed to decrypt field '{field}': {str(e)}")
                    raise ValueError(f"Failed to decrypt field '{field}'") from e
        return self

    @model_validator(mode="after")
    def validate_celery_idempotency(self) -> "Settings":
        """校验 Celery PostgreSQL 幂等状态机的数据库边界。"""
        if self.celery.idempotency_enabled and self.db.type != "postgresql":
            raise ValueError("CELERY_IDEMPOTENCY_ENABLED requires DB_TYPE=postgresql")
        return self

    @property
    def sqlalchemy_database_uri(self) -> str:
        """根据数据库类型动态生成连接 URI。"""
        driver = DB_DRIVER_MAP.get(self.db.type)
        if not driver:
            raise ValueError(f"Unsupported database type: {self.db.type}")

        password = self.secrets.db_password.get_secret_value()

        if self.db.type == "mysql":
            return URL.create(
                drivername=driver,
                username=self.db.username,
                password=password,
                host=self.db.host,
                port=self.db.port,
                database=self.db.database,
                query={"charset": "utf8mb4"},
            ).render_as_string(hide_password=False)
        elif self.db.type == "postgresql":
            return URL.create(
                drivername=driver,
                username=self.db.username,
                password=password,
                host=self.db.host,
                port=self.db.port,
                database=self.db.database,
            ).render_as_string(hide_password=False)
        else:
            raise ValueError(f"Unsupported database type: {self.db.type}")

    @property
    def sqlalchemy_read_database_uri(self) -> str | None:
        """根据数据库类型动态生成只读副本连接 URI。"""
        read_replica = self.db.read_replica
        if read_replica is None:
            return None

        driver = DB_DRIVER_MAP.get(self.db.type)
        if not driver:
            raise ValueError(f"Unsupported database type: {self.db.type}")

        host = read_replica.host
        port = read_replica.port or self.db.port
        username = read_replica.username or self.db.username
        password = (
            self.secrets.db_read_password.get_secret_value()
            if self.secrets.db_read_password is not None
            else self.secrets.db_password.get_secret_value()
        )
        database = read_replica.database or self.db.database

        if self.db.type == "mysql":
            return URL.create(
                drivername=driver,
                username=username,
                password=password,
                host=host,
                port=port,
                database=database,
                query={"charset": "utf8mb4"},
            ).render_as_string(hide_password=False)
        elif self.db.type == "postgresql":
            return URL.create(
                drivername=driver,
                username=username,
                password=password,
                host=host,
                port=port,
                database=database,
            ).render_as_string(hide_password=False)
        else:
            raise ValueError(f"Unsupported database type: {self.db.type}")

    @property
    def redis_url(self) -> str:
        return _build_redis_url(
            host=self.redis.host,
            port=self.redis.port,
            password=self.secrets.redis_password.get_secret_value(),
            db=self.redis.db,
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


def _required_secret_keys() -> set[str]:
    """返回必须由密钥文件显式提供的字段。"""
    return {
        field.validation_alias
        for field in SecretsSettings.model_fields.values()
        if field.is_required() and isinstance(field.validation_alias, str)
    }


def _validate_environment_file(env_file_path: Path) -> dict[str, Any]:
    """验证 TOML 环境配置文件的字段来源和完整性。"""
    with env_file_path.open("rb") as env_file:
        env_values = tomllib.load(env_file)

    if "APP_ENV" in env_values or "app_env" in env_values:
        raise ValueError(
            f"APP_ENV must be configured in the root .env, not {env_file_path}"
        )
    if "secrets" in env_values:
        raise ValueError(f"Secret keys must be moved from {env_file_path} to .secrets")

    misplaced_secret_keys = sorted(set(env_values) & SECRET_FILE_KEYS)
    if misplaced_secret_keys:
        keys = ", ".join(misplaced_secret_keys)
        raise ValueError(
            f"Secret keys must be moved from {env_file_path} to .secrets: {keys}"
        )

    return env_values


def _load_settings_from_files(
    app_env: AppEnv,
    app_config_path: Path,
    secrets_path: Path,
) -> Settings:
    """在明确的 TOML 应用配置和 dotenv 密钥文件上下文中构造 Settings。"""
    secrets_values = dict(dotenv_values(secrets_path))
    secrets = SecretsSettings.model_validate(secrets_values)
    with _use_app_config_path(app_config_path):
        return Settings(app_env=app_env, secrets=secrets)  # type: ignore[call-arg]


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


def _iter_settings_values(
    model: BaseModel,
    prefix: str = "",
) -> Iterator[tuple[str, Any]]:
    """递归遍历嵌套配置，生成点路径和实际字段值。"""
    for name in type(model).model_fields:
        value = getattr(model, name)
        key = f"{prefix}.{name}" if prefix else name
        if isinstance(value, BaseModel):
            yield from _iter_settings_values(value, key)
        else:
            yield key, value


def _config_echo_value(
    key: str,
    value: Any,
    *,
    sensitive_keys: set[str],
    reveal_sensitive: bool,
) -> Any:
    if hasattr(value, "get_secret_value"):
        value = value.get_secret_value()

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


def _echo_loaded_config(settings: Settings) -> None:
    sensitive_keys = {f"secrets.{name}" for name in SecretsSettings.model_fields}
    reveal_sensitive = settings.config_echo.reveal_secrets
    echo_mode = "unmasked" if reveal_sensitive else "masked"

    logger.info("=" * 50)
    logger.success(
        f"Configuration Details (config_echo.reveal_secrets={reveal_sensitive}, {echo_mode}):"
    )
    for key, value in _iter_settings_values(settings):
        echo_value = _config_echo_value(
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
    3. 验证对应环境 TOML 配置文件的字段来源和完整性
    4. 加载配置文件 (app.{env}.toml 和 .secrets)，并显式注入 APP_ENV
    """
    _logger = _setup_startup_logger()
    _logger.info("Loading configuration...")

    # 1-2. 获取唯一 APP_ENV，并验证密钥文件
    try:
        app_env = detect_app_env()
        secrets_path, _ = _validate_secrets_file()
        _logger.info(f"Detected Environment: {app_env}")
    except (FileNotFoundError, ValueError) as e:
        _logger.critical(f"Configuration validation failed: {e}")
        raise

    # 3. 检查对应环境 TOML 文件是否存在
    env_file_path = BASE_DIR / "configs" / f"app.{app_env}.toml"
    if not env_file_path.exists():
        msg = f"CRITICAL: Config file missing for environment '{app_env}' at {env_file_path}"
        _logger.critical(msg)
        raise FileNotFoundError(msg)

    try:
        _validate_environment_file(env_file_path)
    except ValueError as e:
        _logger.critical(f"Configuration validation failed: {e}")
        raise

    # 4. 加载已完成字段隔离校验的 TOML 配置和 dotenv 密钥文件；不读取其他 shell 环境变量
    load_files = [env_file_path, secrets_path]
    _logger.info(f"Loading files: {[f.name for f in load_files]}")

    try:
        _settings = _load_settings_from_files(app_env, env_file_path, secrets_path)
        _logger.success("Configuration loaded successfully.")

        _echo_loaded_config(_settings)

        return _settings
    except Exception as e:
        _logger.critical(f"Config load failed: {e}")
        raise


@cache
def get_settings() -> Settings:
    """获取进程级配置实例。"""
    return load_config()


def init_settings() -> Settings:
    """
    初始化并返回配置实例
    在应用启动时调用此函数
    """
    return get_settings()


def reset_settings() -> None:
    """
    重置配置实例（主要用于测试）
    """
    get_settings.cache_clear()


# 使用 lazy_proxy 创建延迟加载的配置实例
settings = lazy_proxy(get_settings)

__all__ = ["settings", "init_settings", "get_settings", "reset_settings", "Settings"]
