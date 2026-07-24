import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

import internal.config as config_module
from internal.config import Settings, _config_echo_value, _sensitive_secret_config_keys


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )


def _base_env_values() -> dict[str, str]:
    return {
        "DEBUG": "true",
        "OTEL_TRACING_ENABLED": "false",
        "OTEL_SERVICE_NAME": "agent-backend-test",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://collector:4318/v1/traces",
        "OTEL_EXPORTER_OTLP_TIMEOUT_SECONDS": "7",
        "JWT_ALGORITHM": "HS256",
        "DB_TYPE": "postgresql",
        "DB_HOST": "dotenv-db",
        "DB_PORT": "5432",
        "DB_USERNAME": "postgres",
        "DB_DATABASE": "app_test",
        "REDIS_HOST": "dotenv-redis",
        "REDIS_PORT": "6379",
        "REDIS_DB": "0",
        "MILVUS_URI": "http://milvus:19530",
        "MILVUS_DB_NAME": "test_database",
        "MILVUS_TIMEOUT_SECONDS": "7",
    }


def _secret_values() -> dict[str, str]:
    return {
        "APP_ENV": "test",
        "AES_SECRET": "test-aes-secret",
        "JWT_SECRET": "test-jwt-secret",
        "DB_PASSWORD": "dotenv-db-password",
        "REDIS_PASSWORD": "dotenv-redis-password",
    }


def _new_settings(*env_files: Path) -> Settings:
    return Settings(_env_file=list(env_files))  # type: ignore[call-arg]


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


def test_dotenv_values_take_priority_over_shell_environment(
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


def test_shell_environment_is_fallback_when_dotenv_value_is_missing(
    monkeypatch, tmp_path: Path
) -> None:
    env_path = tmp_path / ".env.test"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.pop("REDIS_HOST")
    _write_env_file(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    monkeypatch.setenv("REDIS_HOST", "shell-redis")

    settings = _new_settings(env_path, secrets_path)

    assert settings.REDIS_HOST == "shell-redis"


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
            "APP_ENV": "test",
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
