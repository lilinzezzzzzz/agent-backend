from pathlib import Path

import pytest
from pydantic import ValidationError

from internal.config import Settings, _config_echo_value, _sensitive_secret_config_keys


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )


def _base_env_values() -> dict[str, str]:
    return {
        "DEBUG": "true",
        "JWT_ALGORITHM": "HS256",
        "DB_TYPE": "postgresql",
        "DB_HOST": "dotenv-db",
        "DB_PORT": "5432",
        "DB_USERNAME": "postgres",
        "DB_DATABASE": "app_test",
        "REDIS_HOST": "dotenv-redis",
        "REDIS_PORT": "6379",
        "REDIS_DB": "0",
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
