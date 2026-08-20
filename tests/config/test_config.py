import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from dotenv import dotenv_values
from pydantic import ValidationError
from redis.asyncio import ConnectionPool

import internal.config as config_module
from internal.config import (
    SECRET_FILE_KEYS,
    Settings,
    _config_echo_value,
    _required_config_keys,
    _required_secret_keys,
    _sensitive_secret_config_keys,
)


def test_get_settings_caches_until_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    first = MagicMock(spec=Settings)
    second = MagicMock(spec=Settings)
    load_config = MagicMock(side_effect=[first, second])
    monkeypatch.setattr(config_module, "load_config", load_config)

    assert config_module.get_settings() is first
    assert config_module.init_settings() is first
    load_config.assert_called_once_with()

    config_module.reset_settings()

    assert config_module.get_settings() is second
    assert load_config.call_count == 2


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )


def _base_env_values() -> dict[str, str]:
    return {
        "DEBUG": "true",
        "LOG_FORMAT": "text",
        "LOG_WRITE_TO_FILE": "false",
        "LOG_WRITE_TO_CONSOLE": "true",
        "CONFIG_ECHO_REVEAL_SECRETS": "false",
        "OTEL_TRACING_ENABLED": "false",
        "OTEL_SERVICE_NAME": "agent-backend-test",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://collector:4318/v1/traces",
        "OTEL_EXPORTER_OTLP_TIMEOUT_SECONDS": "7",
        "JWT_ALGORITHM": "HS256",
        "ACCESS_TOKEN_EXPIRE_SECONDS": "1800",
        "BACKEND_CORS_ORIGINS": '["*"]',
        "WECHAT_GRANT_TYPE": "authorization_code",
        "LLM_DEFAULT_PROVIDER": "mimo",
        "LLM_MIMO_BASE_URL": "https://mimo.example/v1",
        "LLM_MIMO_MODEL": "mimo-test",
        "LLM_DEEPSEEK_BASE_URL": "https://deepseek.example/v1",
        "LLM_DEEPSEEK_MODEL": "deepseek-test",
        "EMBEDDING_PROVIDER": "openai_compatible",
        "EMBEDDING_BASE_URL": "http://embedding:8000/v1",
        "EMBEDDING_MODEL": "bge-m3",
        "EMBEDDING_DIMENSION": "1024",
        "EMBEDDING_TIMEOUT_SECONDS": "30",
        "DB_TYPE": "postgresql",
        "DB_HOST": "dotenv-db",
        "DB_PORT": "5432",
        "DB_USERNAME": "postgres",
        "DB_DATABASE": "app_test",
        "DB_ECHO": "false",
        "REDIS_HOST": "dotenv-redis",
        "REDIS_PORT": "6379",
        "REDIS_DB": "0",
        "MILVUS_URI": "http://milvus:19530",
        "MILVUS_DB_NAME": "test_database",
        "MILVUS_TIMEOUT_SECONDS": "7",
        "AGENT_ACTION_CONFIRMATION_SECONDS": "300",
        "AGENT_ACTION_IDEMPOTENCY_SECONDS": "86400",
        "RAG_ALLOWED_DOMAINS": '["order","payment","general"]',
        "RAG_ALLOWED_KB_IDS": "[1,2,3]",
        "RAG_MAX_CONTEXT_CHARS": "12000",
        "CELERY_IDEMPOTENCY_ENABLED": "true",
        "CELERY_EXECUTION_DEADLINE_GRACE_SECONDS": "30",
        "CELERY_QUEUE_START_TIMEOUT_SECONDS": "300",
        "CELERY_PUBLISH_CONFIRM_TIMEOUT_SECONDS": "30",
        "CELERY_PUBLISH_RECONCILER_INTERVAL_SECONDS": "10",
        "CELERY_ORPHAN_FENCE_SECONDS": "300",
        "CELERY_RECONCILER_BEAT_ENABLED": "true",
        "CELERY_RECONCILER_INTERVAL_SECONDS": "60",
        "CELERY_RECONCILER_BATCH_SIZE": "100",
        "CELERY_RECONCILER_QUEUE": "celery_maintenance_queue",
        "CELERY_TASK_MAX_PAYLOAD_BYTES": "262144",
        "ENDPOINT_GUARD_ENABLED": "true",
        "ENDPOINT_GUARD_SOURCE": "settings",
        "ENDPOINT_GUARD_RULES": "[]",
        "ENDPOINT_GUARD_REDIS_KEY": "endpoint_guard:rules",
        "ENDPOINT_GUARD_CACHE_TTL_SECONDS": "10",
        "ENDPOINT_GUARD_FAIL_OPEN": "true",
    }


def _secret_values() -> dict[str, str]:
    return {
        "AES_SECRET": "test-aes-secret",
        "JWT_SECRET": "test-jwt-secret",
        "DB_PASSWORD": "dotenv-db-password",
        "REDIS_PASSWORD": "dotenv-redis-password",
        "WECHAT_APP_ID": "test-wechat-app-id",
        "WECHAT_APP_SECRET": "test-wechat-app-secret",
        "LLM_MIMO_API_KEY": "test-mimo-api-key",
        "LLM_DEEPSEEK_API_KEY": "test-deepseek-api-key",
        "EMBEDDING_API_KEY": "test-embedding-api-key",
    }


def _new_settings(*env_files: Path) -> Settings:
    return Settings(APP_ENV="test", _env_file=list(env_files))  # type: ignore[call-arg]


def test_startup_logger_uses_stderr_without_creating_log_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    startup_logger = MagicMock()
    monkeypatch.setattr(config_module, "logger", startup_logger)
    monkeypatch.setattr(config_module, "BASE_DIR", tmp_path)

    result = config_module._setup_startup_logger()

    assert result is startup_logger
    startup_logger.remove.assert_called_once_with()
    startup_logger.add.assert_called_once_with(
        sys.stderr,
        level="INFO",
        enqueue=False,
        colorize=False,
        diagnose=False,
    )
    assert not (tmp_path / "logs").exists()


def test_detect_app_env_reads_repository_root_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setattr(config_module, "BASE_DIR", tmp_path)
    _write_env_file(tmp_path / ".env", {"APP_ENV": "test"})

    assert config_module.detect_app_env() == "test"


def test_detect_app_env_reads_process_environment_without_root_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setattr(config_module, "BASE_DIR", tmp_path)

    assert config_module.detect_app_env() == "prod"


def test_detect_app_env_rejects_conflicting_process_and_dotenv_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setattr(config_module, "BASE_DIR", tmp_path)
    _write_env_file(tmp_path / ".env", {"APP_ENV": "prod"})

    with pytest.raises(ValueError, match="APP_ENV conflict"):
        config_module.detect_app_env()


def test_detect_app_env_rejects_unsupported_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setattr(config_module, "BASE_DIR", tmp_path)
    _write_env_file(tmp_path / ".env", {"APP_ENV": "production"})

    with pytest.raises(ValueError, match="Unsupported APP_ENV 'production'"):
        config_module.detect_app_env()


def test_load_config_uses_app_env_outside_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setattr(config_module, "BASE_DIR", tmp_path)
    monkeypatch.setattr(
        config_module, "_setup_startup_logger", MagicMock(return_value=MagicMock())
    )
    _write_env_file(tmp_path / ".env", {"APP_ENV": "test"})
    _write_env_file(configs_dir / ".env.test", _base_env_values())
    _write_env_file(tmp_path / ".secrets", _secret_values())

    settings = config_module.load_config()

    assert settings.APP_ENV == "test"
    assert "APP_ENV" not in _secret_values()


def test_load_config_rejects_non_secret_key_in_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setattr(config_module, "BASE_DIR", tmp_path)
    monkeypatch.setattr(
        config_module, "_setup_startup_logger", MagicMock(return_value=MagicMock())
    )
    _write_env_file(tmp_path / ".env", {"APP_ENV": "test"})
    _write_env_file(tmp_path / ".secrets", {**_secret_values(), "APP_ENV": "test"})

    with pytest.raises(ValueError, match="Non-secret or unsupported keys.*APP_ENV"):
        config_module.load_config()


def test_shell_environment_cannot_override_explicit_files(
    monkeypatch, tmp_path: Path
) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    _write_env_file(env_path, _base_env_values())
    _write_env_file(secrets_path, _secret_values())

    monkeypatch.setenv("DEBUG", "release")
    monkeypatch.setenv("DB_HOST", "shell-db")
    monkeypatch.setenv("REDIS_PASSWORD", "shell-redis-password")

    settings = _new_settings(env_path, secrets_path)

    assert settings.DEBUG is True
    assert settings.DB_HOST == "dotenv-db"
    assert settings.REDIS_PASSWORD.get_secret_value() == "dotenv-redis-password"


def test_shell_environment_is_not_a_fallback_for_missing_config(
    monkeypatch, tmp_path: Path
) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.pop("REDIS_HOST")
    _write_env_file(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    monkeypatch.setenv("REDIS_HOST", "shell-redis")

    with pytest.raises(ValidationError, match="REDIS_HOST"):
        _new_settings(env_path, secrets_path)


def test_load_config_rejects_secret_key_in_environment_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setattr(config_module, "BASE_DIR", tmp_path)
    monkeypatch.setattr(
        config_module, "_setup_startup_logger", MagicMock(return_value=MagicMock())
    )
    _write_env_file(tmp_path / ".env", {"APP_ENV": "test"})
    _write_env_file(
        configs_dir / ".env.test",
        {**_base_env_values(), "JWT_SECRET": "misplaced-secret"},
    )
    _write_env_file(tmp_path / ".secrets", _secret_values())

    with pytest.raises(ValueError, match="Secret keys must be moved.*JWT_SECRET"):
        config_module.load_config()


def test_unknown_configuration_key_is_rejected(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    _write_env_file(env_path, {**_base_env_values(), "DB_HSOT": "typo"})
    _write_env_file(secrets_path, _secret_values())

    with pytest.raises(ValidationError, match="DB_HSOT"):
        _new_settings(env_path, secrets_path)


def test_repository_environment_files_define_every_required_config_key() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    required_keys = _required_config_keys()
    secrets_template = repository_root / ".secrets.example"

    for app_env in ("local", "dev", "test", "prod"):
        env_path = repository_root / "configs" / f".env.{app_env}"
        values = dotenv_values(env_path)

        assert required_keys <= set(values), app_env
        assert not (set(values) & SECRET_FILE_KEYS), app_env
        Settings(APP_ENV=app_env, _env_file=[env_path, secrets_template])  # type: ignore[call-arg]


def test_secret_template_defines_every_required_secret_key() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    values = dotenv_values(repository_root / ".secrets.example")

    assert _required_secret_keys() <= set(values)
    assert set(values) <= SECRET_FILE_KEYS


def test_settings_defaults_are_limited_to_conditional_fields() -> None:
    conditional_fields = {
        "DB_READ_DATABASE",
        "DB_READ_HOST",
        "DB_READ_PASSWORD",
        "DB_READ_PORT",
        "DB_READ_USERNAME",
        "LOG_BASE_DIR",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    }

    fields_with_defaults = {
        name for name, field in Settings.model_fields.items() if not field.is_required()
    }

    assert fields_with_defaults == conditional_fields


def test_otlp_exporter_config_values_are_loaded(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    _write_env_file(env_path, _base_env_values())
    _write_env_file(secrets_path, _secret_values())

    settings = _new_settings(env_path, secrets_path)

    assert (
        str(settings.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT)
        == "http://collector:4318/v1/traces"
    )
    assert settings.OTEL_EXPORTER_OTLP_TIMEOUT_SECONDS == 7


def test_log_output_config_values_are_loaded(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.update(
        {
            "LOG_BASE_DIR": "/var/log/agent-backend",
            "LOG_WRITE_TO_FILE": "false",
            "LOG_WRITE_TO_CONSOLE": "true",
        }
    )
    _write_env_file(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    settings = _new_settings(env_path, secrets_path)

    assert settings.LOG_BASE_DIR == Path("/var/log/agent-backend")
    assert settings.LOG_WRITE_TO_FILE is False
    assert settings.LOG_WRITE_TO_CONSOLE is True


def test_relative_log_base_dir_is_resolved_from_project_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.update(
        {
            "LOG_BASE_DIR": "logs",
            "LOG_WRITE_TO_FILE": "true",
        }
    )
    _write_env_file(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())
    monkeypatch.setattr(config_module, "BASE_DIR", tmp_path)

    settings = _new_settings(env_path, secrets_path)

    assert settings.LOG_BASE_DIR == tmp_path / "logs"


def test_log_output_targets_must_be_explicit(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.pop("LOG_WRITE_TO_FILE")
    _write_env_file(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    with pytest.raises(ValidationError, match="LOG_WRITE_TO_FILE"):
        _new_settings(env_path, secrets_path)


def test_file_logging_requires_base_log_dir(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values["LOG_WRITE_TO_FILE"] = "true"
    _write_env_file(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    with pytest.raises(
        ValidationError,
        match="LOG_BASE_DIR is required when LOG_WRITE_TO_FILE=true",
    ):
        _new_settings(env_path, secrets_path)


def test_log_output_requires_at_least_one_target(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.update(
        {
            "LOG_WRITE_TO_FILE": "false",
            "LOG_WRITE_TO_CONSOLE": "false",
        }
    )
    _write_env_file(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    with pytest.raises(
        ValidationError,
        match="LOG_WRITE_TO_FILE and LOG_WRITE_TO_CONSOLE cannot both be false",
    ):
        _new_settings(env_path, secrets_path)


def test_tracing_requires_otlp_traces_endpoint(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values["OTEL_TRACING_ENABLED"] = "true"
    env_values.pop("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    _write_env_file(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    with pytest.raises(
        ValidationError,
        match="OTEL_EXPORTER_OTLP_TRACES_ENDPOINT is required",
    ):
        _new_settings(env_path, secrets_path)


def test_milvus_config_values_are_loaded(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    _write_env_file(env_path, _base_env_values())
    _write_env_file(secrets_path, _secret_values())

    settings = _new_settings(env_path, secrets_path)

    assert str(settings.MILVUS_URI) == "http://milvus:19530/"
    assert settings.MILVUS_DB_NAME == "test_database"
    assert settings.MILVUS_TIMEOUT_SECONDS == 7


def test_milvus_database_name_cannot_be_empty(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values["MILVUS_DB_NAME"] = "  "
    _write_env_file(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    with pytest.raises(ValidationError, match="MILVUS_DB_NAME cannot be empty"):
        _new_settings(env_path, secrets_path)


@pytest.mark.parametrize(
    ("db_type", "port", "driver", "query"),
    [
        ("mysql", "3306", "mysql+aiomysql", "?charset=utf8mb4"),
        ("postgresql", "5432", "postgresql+asyncpg", ""),
    ],
)
def test_sqlalchemy_database_urls_use_sqlalchemy_escaping(
    tmp_path: Path,
    db_type: str,
    port: str,
    driver: str,
    query: str,
) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.update(
        {
            "DB_TYPE": db_type,
            "DB_HOST": "primary-db.example",
            "DB_PORT": port,
            "DB_USERNAME": "primary@example.com",
            "DB_DATABASE": "primary_db",
            "DB_READ_HOST": "replica-db.example",
            "DB_READ_PORT": port,
            "DB_READ_USERNAME": "reader@example.com",
            "DB_READ_DATABASE": "replica_db",
            "CELERY_IDEMPOTENCY_ENABLED": "false",
        }
    )
    secret_values = _secret_values()
    secret_values.update(
        {
            "DB_PASSWORD": "primary@pass:/?[]",
            "DB_READ_PASSWORD": "reader@pass:/?[]",
        }
    )
    _write_env_file(env_path, env_values)
    _write_env_file(secrets_path, secret_values)

    settings = _new_settings(env_path, secrets_path)

    assert settings.sqlalchemy_database_uri == (
        f"{driver}://primary%40example.com:primary%40pass%3A%2F%3F%5B%5D"
        f"@primary-db.example:{port}/primary_db{query}"
    )
    assert settings.sqlalchemy_read_database_uri == (
        f"{driver}://reader%40example.com:reader%40pass%3A%2F%3F%5B%5D"
        f"@replica-db.example:{port}/replica_db{query}"
    )


def test_redis_url_escapes_password_and_supports_ipv6(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.update(
        {
            "REDIS_HOST": "2001:db8::1",
            "REDIS_PORT": "6380",
            "REDIS_DB": "4",
        }
    )
    secret_values = _secret_values()
    secret_values["REDIS_PASSWORD"] = "redis@pass:/?#[]%"
    _write_env_file(env_path, env_values)
    _write_env_file(secrets_path, secret_values)

    settings = _new_settings(env_path, secrets_path)

    assert settings.redis_url == (
        "redis://:redis%40pass%3A%2F%3F%23%5B%5D%25@[2001:db8::1]:6380/4"
    )
    pool = ConnectionPool.from_url(settings.redis_url)
    assert pool.connection_kwargs["host"] == "2001:db8::1"
    assert pool.connection_kwargs["port"] == 6380
    assert pool.connection_kwargs["password"] == "redis@pass:/?#[]%"
    assert pool.connection_kwargs["db"] == 4


def test_oracle_database_type_is_rejected(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.update(
        {
            "DB_TYPE": "oracle",
            "CELERY_IDEMPOTENCY_ENABLED": "false",
        }
    )
    _write_env_file(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    with pytest.raises(ValidationError, match="DB_TYPE must be one of"):
        _new_settings(env_path, secrets_path)


def test_celery_publish_confirmation_timeout_exceeds_reconciler_interval(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.update(
        {
            "CELERY_PUBLISH_CONFIRM_TIMEOUT_SECONDS": "10",
            "CELERY_PUBLISH_RECONCILER_INTERVAL_SECONDS": "10",
        }
    )
    _write_env_file(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    with pytest.raises(
        ValidationError,
        match=(
            "CELERY_PUBLISH_CONFIRM_TIMEOUT_SECONDS must exceed "
            "CELERY_PUBLISH_RECONCILER_INTERVAL_SECONDS"
        ),
    ):
        _new_settings(env_path, secrets_path)


def test_celery_queue_start_timeout_exceeds_reconciler_interval(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.update(
        {
            "CELERY_QUEUE_START_TIMEOUT_SECONDS": "60",
            "CELERY_RECONCILER_INTERVAL_SECONDS": "60",
        }
    )
    _write_env_file(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    with pytest.raises(
        ValidationError,
        match=(
            "CELERY_QUEUE_START_TIMEOUT_SECONDS must exceed "
            "CELERY_RECONCILER_INTERVAL_SECONDS"
        ),
    ):
        _new_settings(env_path, secrets_path)


def test_celery_state_machine_removes_legacy_timing_settings() -> None:
    fields = Settings.model_fields

    assert "CELERY_EXECUTION_DEADLINE_GRACE_SECONDS" in fields
    assert "CELERY_QUEUE_START_TIMEOUT_SECONDS" in fields
    assert "CELERY_ORPHAN_FENCE_SECONDS" in fields
    assert "CELERY_EXECUTION_LEASE_GRACE_SECONDS" not in fields
    assert "CELERY_TASK_DELIVERY_GRACE_SECONDS" not in fields
    assert "CELERY_PUBLISHED_ORPHAN_SECONDS" not in fields
    assert "CELERY_TASK_IDEMPOTENCY_DAYS" not in fields


def test_sensitive_secret_config_keys_only_match_sensitive_names() -> None:
    assert _sensitive_secret_config_keys(
        {
            "CONFIG_ECHO_REVEAL_SECRETS": "false",
            "JWT_SECRET": "jwt-secret",
            "DB_PASSWORD": "db-password",
            "LLM_DEFAULT_PROVIDER": "deepseek",
            "LLM_DEEPSEEK_API_KEY": "api-key",
            "EMBEDDING_API_KEY": "embedding-api-key",
        }
    ) == {"JWT_SECRET", "DB_PASSWORD", "LLM_DEEPSEEK_API_KEY", "EMBEDDING_API_KEY"}


def test_embedding_config_values_are_loaded(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.update(
        {
            "EMBEDDING_PROVIDER": "openai_compatible",
            "EMBEDDING_BASE_URL": "http://embedding.example/v1",
            "EMBEDDING_MODEL": "bge-m3",
            "EMBEDDING_DIMENSION": "1024",
            "EMBEDDING_TIMEOUT_SECONDS": "30",
        }
    )
    secret_values = _secret_values()
    secret_values["EMBEDDING_API_KEY"] = "embedding-api-key"
    _write_env_file(env_path, env_values)
    _write_env_file(secrets_path, secret_values)

    settings = _new_settings(env_path, secrets_path)

    assert settings.EMBEDDING_PROVIDER == "openai_compatible"
    assert settings.EMBEDDING_BASE_URL == "http://embedding.example/v1"
    assert settings.EMBEDDING_MODEL == "bge-m3"
    assert settings.EMBEDDING_API_KEY.get_secret_value() == "embedding-api-key"
    assert settings.EMBEDDING_DIMENSION == 1024
    assert settings.EMBEDDING_TIMEOUT_SECONDS == 30


def test_config_echo_masks_sensitive_secret_values(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    _write_env_file(env_path, _base_env_values())
    _write_env_file(secrets_path, _secret_values())
    settings = _new_settings(env_path, secrets_path)

    assert (
        _config_echo_value(
            settings,
            "JWT_SECRET",
            settings.model_dump()["JWT_SECRET"],
            sensitive_keys={"JWT_SECRET"},
            reveal_sensitive=False,
        )
        == "test...cret (len=15)"
    )
    assert (
        _config_echo_value(
            settings,
            "DB_HOST",
            settings.model_dump()["DB_HOST"],
            sensitive_keys={"JWT_SECRET"},
            reveal_sensitive=False,
        )
        == "dotenv-db"
    )
    assert (
        _config_echo_value(
            settings,
            "JWT_SECRET",
            settings.model_dump()["JWT_SECRET"],
            sensitive_keys={"JWT_SECRET"},
            reveal_sensitive=True,
        )
        == "test-jwt-secret"
    )


def test_config_echo_fully_masks_short_sensitive_values(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    _write_env_file(env_path, _base_env_values())
    secret_values = _secret_values()
    secret_values["JWT_SECRET"] = "abc"
    _write_env_file(secrets_path, secret_values)
    settings = _new_settings(env_path, secrets_path)

    assert (
        _config_echo_value(
            settings,
            "JWT_SECRET",
            settings.model_dump()["JWT_SECRET"],
            sensitive_keys={"JWT_SECRET"},
            reveal_sensitive=False,
        )
        == "*** (len=3)"
    )


def test_config_echo_reports_empty_sensitive_values(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    secret_values = _secret_values()
    secret_values["JWT_SECRET"] = ""
    _write_env_file(env_path, _base_env_values())
    _write_env_file(secrets_path, secret_values)
    settings = _new_settings(env_path, secrets_path)

    assert (
        _config_echo_value(
            settings,
            "JWT_SECRET",
            settings.model_dump()["JWT_SECRET"],
            sensitive_keys={"JWT_SECRET"},
            reveal_sensitive=False,
        )
        == "<empty>"
    )
