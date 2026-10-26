import json
import sys
import tomllib
from pathlib import Path
from typing import get_origin
from unittest.mock import MagicMock

import pytest
from dotenv import dotenv_values
from pydantic import BaseModel, ValidationError
from redis.asyncio import ConnectionPool

import internal.config as config_module
from internal.config import (
    SECRET_FILE_KEYS,
    CelerySettings,
    DatabaseReadReplicaSettings,
    DatabaseSettings,
    LogSettings,
    OtelSettings,
    SecretsSettings,
    Settings,
    _config_echo_value,
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


def _toml_literal(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        return (
            "{"
            + ", ".join(f"{key} = {_toml_literal(item)}" for key, item in value.items())
            + "}"
        )
    raise TypeError(f"unsupported TOML test value: {value!r}")


_FIELD_PATHS: dict[str, tuple[str, str]] = {
    "DEBUG": ("", "debug"),
    "LOG_FORMAT": ("log", "format"),
    "LOG_BASE_DIR": ("log", "base_dir"),
    "LOG_WRITE_TO_FILE": ("log", "write_to_file"),
    "LOG_WRITE_TO_CONSOLE": ("log", "write_to_console"),
    "OTEL_TRACING_ENABLED": ("otel", "tracing_enabled"),
    "OTEL_OPERATION_SPANS_ENABLED": ("otel", "operation_spans_enabled"),
    "OTEL_METRICS_ENABLED": ("otel", "metrics_enabled"),
    "OTEL_LOGS_ENABLED": ("otel", "logs_enabled"),
    "OTEL_AUTO_CONFIGURE": ("otel", "auto_configure"),
    "OTEL_SERVICE_NAME": ("otel", "service_name"),
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": ("otel", "exporter_otlp_traces_endpoint"),
    "OTEL_EXPORTER_OTLP_TIMEOUT_SECONDS": ("otel", "exporter_otlp_timeout_seconds"),
    "JWT_ALGORITHM": ("auth.jwt", "algorithm"),
    "ACCESS_TOKEN_EXPIRE_SECONDS": ("auth.access_token", "expire_seconds"),
    "CONFIG_ECHO_REVEAL_SECRETS": ("config_echo", "reveal_secrets"),
    "BACKEND_CORS_ORIGINS": ("cors", "origins"),
    "WECHAT_GRANT_TYPE": ("wechat", "grant_type"),
    "LLM_DEFAULT_PROVIDER": ("llm", "default_provider"),
    "LLM_MIMO_BASE_URL": ("llm.mimo", "base_url"),
    "LLM_MIMO_MODEL": ("llm.mimo", "model"),
    "LLM_DEEPSEEK_BASE_URL": ("llm.deepseek", "base_url"),
    "LLM_DEEPSEEK_MODEL": ("llm.deepseek", "model"),
    "EMBEDDING_PROVIDER": ("embedding", "provider"),
    "EMBEDDING_BASE_URL": ("embedding", "base_url"),
    "EMBEDDING_MODEL": ("embedding", "model"),
    "EMBEDDING_DIMENSION": ("embedding", "dimension"),
    "EMBEDDING_TIMEOUT_SECONDS": ("embedding", "timeout_seconds"),
    "MILVUS_URI": ("milvus", "uri"),
    "MILVUS_DB_NAME": ("milvus", "db_name"),
    "MILVUS_TIMEOUT_SECONDS": ("milvus", "timeout_seconds"),
    "AGENT_ACTION_CONFIRMATION_SECONDS": ("agent", "action_confirmation_seconds"),
    "AGENT_ACTION_IDEMPOTENCY_SECONDS": ("agent", "action_idempotency_seconds"),
    "AGENT_LEASE_SECONDS": ("agent", "lease_seconds"),
    "AGENT_CONTROL_POLL_SECONDS": ("agent", "control_poll_seconds"),
    "AGENT_CANCEL_GRACE_SECONDS": ("agent", "cancel_grace_seconds"),
    "AGENT_STREAM_BUFFER_SIZE": ("agent", "stream_buffer_size"),
    "AGENT_CHECKPOINT_MAX_BYTES": ("agent", "checkpoint_max_bytes"),
    "AGENT_LLM_TIMEOUT_SECONDS": ("agent", "llm_timeout_seconds"),
    "RAG_ALLOWED_DOMAINS": ("rag", "allowed_domains"),
    "RAG_ALLOWED_KB_IDS": ("rag", "allowed_kb_ids"),
    "RAG_MAX_CONTEXT_CHARS": ("rag", "max_context_chars"),
    "DB_TYPE": ("db", "type"),
    "DB_HOST": ("db", "host"),
    "DB_PORT": ("db", "port"),
    "DB_USERNAME": ("db", "username"),
    "DB_DATABASE": ("db", "database"),
    "DB_ECHO": ("db", "echo"),
    "DB_READ_HOST": ("db.read_replica", "host"),
    "DB_READ_PORT": ("db.read_replica", "port"),
    "DB_READ_USERNAME": ("db.read_replica", "username"),
    "DB_READ_DATABASE": ("db.read_replica", "database"),
    "REDIS_HOST": ("redis", "host"),
    "REDIS_PORT": ("redis", "port"),
    "REDIS_DB": ("redis", "db"),
    "CELERY_IDEMPOTENCY_ENABLED": ("celery", "idempotency_enabled"),
    "CELERY_EXECUTION_DEADLINE_GRACE_SECONDS": (
        "celery",
        "execution_deadline_grace_seconds",
    ),
    "CELERY_QUEUE_START_TIMEOUT_SECONDS": ("celery", "queue_start_timeout_seconds"),
    "CELERY_PUBLISH_CONFIRM_TIMEOUT_SECONDS": (
        "celery",
        "publish_confirm_timeout_seconds",
    ),
    "CELERY_PUBLISH_RECONCILER_INTERVAL_SECONDS": (
        "celery",
        "publish_reconciler_interval_seconds",
    ),
    "CELERY_ORPHAN_FENCE_SECONDS": ("celery", "orphan_fence_seconds"),
    "CELERY_RECONCILER_BEAT_ENABLED": ("celery", "reconciler_beat_enabled"),
    "CELERY_RECONCILER_INTERVAL_SECONDS": ("celery", "reconciler_interval_seconds"),
    "CELERY_RECONCILER_BATCH_SIZE": ("celery", "reconciler_batch_size"),
    "CELERY_RECONCILER_QUEUE": ("celery", "reconciler_queue"),
    "CELERY_TASK_MAX_PAYLOAD_BYTES": ("celery", "task_max_payload_bytes"),
    "ENDPOINT_GUARD_ENABLED": ("endpoint_guard", "enabled"),
    "ENDPOINT_GUARD_SOURCE": ("endpoint_guard", "source"),
    "ENDPOINT_GUARD_RULES": ("endpoint_guard", "rules"),
    "ENDPOINT_GUARD_REDIS_KEY": ("endpoint_guard", "redis_key"),
    "ENDPOINT_GUARD_CACHE_TTL_SECONDS": ("endpoint_guard", "cache_ttl_seconds"),
    "ENDPOINT_GUARD_FAIL_OPEN": ("endpoint_guard", "fail_open"),
}


def _field_annotation(path: str):
    model = Settings
    annotation = None
    for part in path.split("."):
        field = model.model_fields.get(part)
        if field is None:
            return None
        annotation = field.annotation
        if isinstance(annotation, type) and hasattr(annotation, "model_fields"):
            model = annotation
    return annotation


def _write_app_config(path: Path, values: dict[str, object]) -> None:
    root_lines: list[str] = []
    sections: dict[str, list[str]] = {}
    for key, value in values.items():
        path_parts = _FIELD_PATHS.get(key)
        if path_parts is None:
            section, leaf = None, key
            annotation = None
        else:
            section, leaf = path_parts
            annotation = _field_annotation(".".join(path_parts))
        if isinstance(value, str):
            if annotation is bool:
                normalized = value.lower()
                if normalized not in {"true", "false"}:
                    raise ValueError(f"{key}: expected true/false, got {value!r}")
                value = normalized == "true"
            elif annotation is int:
                value = int(value)
            elif get_origin(annotation) is list:
                value = json.loads(value)
        rendered = f"{leaf} = {_toml_literal(value)}"
        if section:
            sections.setdefault(section, []).append(rendered)
        else:
            root_lines.append(rendered)
    lines = list(root_lines)
    for section, section_lines in sections.items():
        lines.append(f"[{section}]")
        lines.extend(section_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _base_env_values() -> dict[str, str]:
    return {
        "DEBUG": "true",
        "LOG_FORMAT": "text",
        "LOG_WRITE_TO_FILE": "false",
        "LOG_WRITE_TO_CONSOLE": "true",
        "CONFIG_ECHO_REVEAL_SECRETS": "false",
        "OTEL_TRACING_ENABLED": "false",
        "OTEL_OPERATION_SPANS_ENABLED": "true",
        "OTEL_METRICS_ENABLED": "false",
        "OTEL_LOGS_ENABLED": "false",
        "OTEL_AUTO_CONFIGURE": "false",
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
        "AGENT_LEASE_SECONDS": "30",
        "AGENT_CONTROL_POLL_SECONDS": "5",
        "AGENT_CANCEL_GRACE_SECONDS": "70",
        "AGENT_STREAM_BUFFER_SIZE": "16",
        "AGENT_CHECKPOINT_MAX_BYTES": "1048576",
        "AGENT_LLM_TIMEOUT_SECONDS": "60",
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


def _new_settings(app_config_path: Path, secrets_path: Path) -> Settings:
    return config_module._load_settings_from_files(
        "test",
        app_config_path,
        secrets_path,
    )


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
    _write_app_config(configs_dir / "app.test.toml", _base_env_values())
    _write_env_file(tmp_path / ".secrets", _secret_values())

    settings = config_module.load_config()

    assert settings.app_env == "test"
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
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    _write_app_config(env_path, _base_env_values())
    _write_env_file(secrets_path, _secret_values())

    monkeypatch.setenv("DEBUG", "release")
    monkeypatch.setenv("DB_HOST", "shell-db")
    monkeypatch.setenv("REDIS_PASSWORD", "shell-redis-password")

    settings = _new_settings(env_path, secrets_path)

    assert settings.debug is True
    assert settings.db.host == "dotenv-db"
    assert settings.secrets.redis_password.get_secret_value() == "dotenv-redis-password"


def test_shell_environment_is_not_a_fallback_for_missing_config(
    monkeypatch, tmp_path: Path
) -> None:
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.pop("REDIS_HOST")
    _write_app_config(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    monkeypatch.setenv("REDIS_HOST", "shell-redis")

    with pytest.raises(ValidationError, match=r"redis\.host"):
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
    _write_app_config(
        configs_dir / "app.test.toml",
        {**_base_env_values(), "JWT_SECRET": "misplaced-secret"},
    )
    _write_env_file(tmp_path / ".secrets", _secret_values())

    with pytest.raises(ValueError, match="Secret keys must be moved.*JWT_SECRET"):
        config_module.load_config()


def test_load_config_rejects_app_env_in_toml(
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
    _write_app_config(
        configs_dir / "app.test.toml",
        {**_base_env_values(), "APP_ENV": "test"},
    )
    _write_env_file(tmp_path / ".secrets", _secret_values())

    with pytest.raises(ValueError, match="APP_ENV must be configured in the root .env"):
        config_module.load_config()


def test_unknown_configuration_key_is_rejected(tmp_path: Path) -> None:
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    _write_app_config(env_path, {**_base_env_values(), "DB_HSOT": "typo"})
    _write_env_file(secrets_path, _secret_values())

    with pytest.raises(ValidationError, match="DB_HSOT"):
        _new_settings(env_path, secrets_path)


@pytest.mark.parametrize(
    ("section", "key"),
    [
        ("", "unrecognized_setting"),
        ("db", "DB_PASSWORD"),
        ("db", "db_password"),
        ("db", "password"),
        ("llm.mimo", "api_key"),
        ("db", "type"),
    ],
)
def test_load_config_validation_errors_hide_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    section: str,
    key: str,
) -> None:
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setattr(config_module, "BASE_DIR", tmp_path)
    startup_logger = MagicMock()
    monkeypatch.setattr(
        config_module,
        "_setup_startup_logger",
        MagicMock(return_value=startup_logger),
    )
    _write_env_file(tmp_path / ".env", {"APP_ENV": "test"})
    _write_env_file(tmp_path / ".secrets", _secret_values())

    sensitive_value = "synthetic-config-secret-7821"
    env_values = _base_env_values()
    if (section, key) == ("db", "type"):
        env_values["DB_TYPE"] = sensitive_value
    config_path = configs_dir / "app.test.toml"
    _write_app_config(config_path, env_values)
    if key != "type":
        config_text = config_path.read_text(encoding="utf-8")
        misplaced_field = f"{key} = {json.dumps(sensitive_value)}\n"
        if section:
            table_header = f"[{section}]\n"
            config_text = config_text.replace(
                table_header,
                table_header + misplaced_field,
                1,
            )
        else:
            config_text = misplaced_field + config_text
        config_path.write_text(config_text, encoding="utf-8")

    with pytest.raises(ValidationError) as exc_info:
        config_module.load_config()

    expected_location = (*section.split("."), key) if section else (key,)
    assert expected_location in {error["loc"] for error in exc_info.value.errors()}
    logged_errors = "\n".join(
        call.args[0] for call in startup_logger.critical.call_args_list
    )
    assert "Config load failed" in logged_errors
    assert key in logged_errors
    assert sensitive_value not in logged_errors
    assert sensitive_value not in str(exc_info.value)


@pytest.mark.parametrize("model_type", [DatabaseSettings, SecretsSettings])
def test_group_validation_errors_hide_values(model_type: type[BaseModel]) -> None:
    sensitive_value = "synthetic-config-secret-7821"

    with pytest.raises(ValidationError) as exc_info:
        model_type.model_validate({"password": sensitive_value})

    assert "password" in str(exc_info.value)
    assert sensitive_value not in str(exc_info.value)


def test_repository_environment_files_define_every_required_config_key() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    secrets_template = repository_root / ".secrets.example"

    for app_env in ("local", "dev", "test", "prod"):
        env_path = repository_root / "configs" / f"app.{app_env}.toml"
        with env_path.open("rb") as env_file:
            values = tomllib.load(env_file)

        assert not (set(values) & SECRET_FILE_KEYS), app_env
        settings = config_module._load_settings_from_files(
            app_env, env_path, secrets_template
        )
        assert isinstance(settings.debug, bool)
        assert isinstance(settings.db.port, int)
        assert isinstance(settings.cors.origins, list)
        assert isinstance(settings.endpoint_guard.rules, list)


def test_secret_template_defines_every_required_secret_key() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    values = dotenv_values(repository_root / ".secrets.example")

    assert _required_secret_keys() <= set(values)
    assert set(values) <= SECRET_FILE_KEYS


def test_settings_defaults_are_limited_to_conditional_fields() -> None:
    assert not LogSettings.model_fields["base_dir"].is_required()
    assert not OtelSettings.model_fields["exporter_otlp_traces_endpoint"].is_required()
    assert not DatabaseSettings.model_fields["read_replica"].is_required()
    assert not DatabaseReadReplicaSettings.model_fields["port"].is_required()
    assert not DatabaseReadReplicaSettings.model_fields["username"].is_required()
    assert not DatabaseReadReplicaSettings.model_fields["database"].is_required()


def test_otlp_exporter_config_values_are_loaded(tmp_path: Path) -> None:
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    _write_app_config(env_path, _base_env_values())
    _write_env_file(secrets_path, _secret_values())

    settings = _new_settings(env_path, secrets_path)

    assert (
        str(settings.otel.exporter_otlp_traces_endpoint)
        == "http://collector:4318/v1/traces"
    )
    assert settings.otel.exporter_otlp_timeout_seconds == 7


def test_telemetry_switches_are_loaded_independently_from_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.update(
        {
            "OTEL_OPERATION_SPANS_ENABLED": "false",
            "OTEL_METRICS_ENABLED": "true",
            "OTEL_LOGS_ENABLED": "true",
        }
    )
    for name in (
        "OTEL_TRACING_ENABLED",
        "OTEL_OPERATION_SPANS_ENABLED",
        "OTEL_AUTO_CONFIGURE",
    ):
        monkeypatch.setenv(name, "true")
    monkeypatch.setenv("OTEL_METRICS_ENABLED", "false")
    monkeypatch.setenv("OTEL_LOGS_ENABLED", "false")
    _write_app_config(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    settings = _new_settings(env_path, secrets_path)

    assert settings.otel.tracing_enabled is False
    assert settings.otel.operation_spans_enabled is False
    assert settings.otel.metrics_enabled is True
    assert settings.otel.logs_enabled is True
    assert settings.otel.auto_configure is False


def test_automatic_exporter_setup_is_rejected_in_settings_managed_mode(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values["OTEL_AUTO_CONFIGURE"] = "true"
    _write_app_config(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    with pytest.raises(ValidationError, match="OTEL_AUTO_CONFIGURE must be false"):
        _new_settings(env_path, secrets_path)


@pytest.mark.parametrize(
    "name",
    [
        "OTEL_OPERATION_SPANS_ENABLED",
        "OTEL_METRICS_ENABLED",
        "OTEL_LOGS_ENABLED",
        "OTEL_AUTO_CONFIGURE",
    ],
)
def test_telemetry_switches_require_an_explicit_file_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.pop(name)
    monkeypatch.setenv(name, "false")
    _write_app_config(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    with pytest.raises(ValidationError, match=name.removeprefix("OTEL_").lower()):
        _new_settings(env_path, secrets_path)


def test_log_output_config_values_are_loaded(tmp_path: Path) -> None:
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.update(
        {
            "LOG_BASE_DIR": "/var/log/agent-backend",
            "LOG_WRITE_TO_FILE": "false",
            "LOG_WRITE_TO_CONSOLE": "true",
        }
    )
    _write_app_config(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    settings = _new_settings(env_path, secrets_path)

    assert settings.log.base_dir == Path("/var/log/agent-backend")
    assert settings.log.write_to_file is False
    assert settings.log.write_to_console is True


def test_relative_log_base_dir_is_resolved_from_project_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.update(
        {
            "LOG_BASE_DIR": "logs",
            "LOG_WRITE_TO_FILE": "true",
        }
    )
    _write_app_config(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())
    monkeypatch.setattr(config_module, "BASE_DIR", tmp_path)

    settings = _new_settings(env_path, secrets_path)

    assert settings.log.base_dir == tmp_path / "logs"


def test_log_output_targets_must_be_explicit(tmp_path: Path) -> None:
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.pop("LOG_WRITE_TO_FILE")
    _write_app_config(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    with pytest.raises(ValidationError, match=r"log\.write_to_file"):
        _new_settings(env_path, secrets_path)


def test_file_logging_requires_base_log_dir(tmp_path: Path) -> None:
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values["LOG_WRITE_TO_FILE"] = "true"
    _write_app_config(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    with pytest.raises(
        ValidationError,
        match="LOG_BASE_DIR is required when LOG_WRITE_TO_FILE=true",
    ):
        _new_settings(env_path, secrets_path)


def test_log_output_requires_at_least_one_target(tmp_path: Path) -> None:
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.update(
        {
            "LOG_WRITE_TO_FILE": "false",
            "LOG_WRITE_TO_CONSOLE": "false",
        }
    )
    _write_app_config(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    with pytest.raises(
        ValidationError,
        match="LOG_WRITE_TO_FILE and LOG_WRITE_TO_CONSOLE cannot both be false",
    ):
        _new_settings(env_path, secrets_path)


def test_tracing_requires_otlp_traces_endpoint(tmp_path: Path) -> None:
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values["OTEL_TRACING_ENABLED"] = "true"
    env_values.pop("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    _write_app_config(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    with pytest.raises(
        ValidationError,
        match="OTEL_EXPORTER_OTLP_TRACES_ENDPOINT is required",
    ):
        _new_settings(env_path, secrets_path)


def test_milvus_config_values_are_loaded(tmp_path: Path) -> None:
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    _write_app_config(env_path, _base_env_values())
    _write_env_file(secrets_path, _secret_values())

    settings = _new_settings(env_path, secrets_path)

    assert str(settings.milvus.uri) == "http://milvus:19530/"
    assert settings.milvus.db_name == "test_database"
    assert settings.milvus.timeout_seconds == 7


def test_milvus_database_name_cannot_be_empty(tmp_path: Path) -> None:
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values["MILVUS_DB_NAME"] = "  "
    _write_app_config(env_path, env_values)
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
    env_path = tmp_path / "app.test.toml"
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
    _write_app_config(env_path, env_values)
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
    env_path = tmp_path / "app.test.toml"
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
    _write_app_config(env_path, env_values)
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
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.update(
        {
            "DB_TYPE": "oracle",
            "CELERY_IDEMPOTENCY_ENABLED": "false",
        }
    )
    _write_app_config(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    with pytest.raises(ValidationError, match="DB_TYPE must be one of"):
        _new_settings(env_path, secrets_path)


def test_celery_publish_confirmation_timeout_exceeds_reconciler_interval(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.update(
        {
            "CELERY_PUBLISH_CONFIRM_TIMEOUT_SECONDS": "10",
            "CELERY_PUBLISH_RECONCILER_INTERVAL_SECONDS": "10",
        }
    )
    _write_app_config(env_path, env_values)
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
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.update(
        {
            "CELERY_QUEUE_START_TIMEOUT_SECONDS": "60",
            "CELERY_RECONCILER_INTERVAL_SECONDS": "60",
        }
    )
    _write_app_config(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    with pytest.raises(
        ValidationError,
        match=(
            "CELERY_QUEUE_START_TIMEOUT_SECONDS must exceed "
            "CELERY_RECONCILER_INTERVAL_SECONDS"
        ),
    ):
        _new_settings(env_path, secrets_path)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("CELERY_EXECUTION_DEADLINE_GRACE_SECONDS", "301"),
        ("CELERY_QUEUE_START_TIMEOUT_SECONDS", "1"),
        ("CELERY_PUBLISH_CONFIRM_TIMEOUT_SECONDS", "1"),
        ("CELERY_RECONCILER_QUEUE", " "),
    ],
)
def test_celery_idempotency_constraints_apply_only_when_enabled(
    tmp_path: Path,
    enabled: bool,
    key: str,
    value: str,
) -> None:
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    env_values = _base_env_values()
    env_values.update(
        {
            "DB_TYPE": "postgresql" if enabled else "mysql",
            "CELERY_IDEMPOTENCY_ENABLED": str(enabled).lower(),
            "CELERY_RECONCILER_BEAT_ENABLED": "false",
            key: value,
        }
    )
    _write_app_config(env_path, env_values)
    _write_env_file(secrets_path, _secret_values())

    if enabled:
        with pytest.raises(ValidationError, match=key):
            _new_settings(env_path, secrets_path)
        return

    settings = _new_settings(env_path, secrets_path)

    assert settings.celery.idempotency_enabled is False
    assert settings.celery.reconciler_beat_enabled is False
    assert settings.db.type == "mysql"


def test_celery_state_machine_removes_legacy_timing_settings() -> None:
    fields = CelerySettings.model_fields

    assert "execution_deadline_grace_seconds" in fields
    assert "queue_start_timeout_seconds" in fields
    assert "orphan_fence_seconds" in fields
    assert "execution_lease_grace_seconds" not in fields
    assert "task_delivery_grace_seconds" not in fields
    assert "published_orphan_seconds" not in fields
    assert "task_idempotency_days" not in fields


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
    env_path = tmp_path / "app.test.toml"
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
    _write_app_config(env_path, env_values)
    _write_env_file(secrets_path, secret_values)

    settings = _new_settings(env_path, secrets_path)

    assert settings.embedding.provider == "openai_compatible"
    assert settings.embedding.base_url == "http://embedding.example/v1"
    assert settings.embedding.model == "bge-m3"
    assert settings.secrets.embedding_api_key.get_secret_value() == "embedding-api-key"
    assert settings.embedding.dimension == 1024
    assert settings.embedding.timeout_seconds == 30


def test_config_echo_masks_sensitive_secret_values(tmp_path: Path) -> None:
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    _write_app_config(env_path, _base_env_values())
    _write_env_file(secrets_path, _secret_values())
    settings = _new_settings(env_path, secrets_path)

    assert (
        _config_echo_value(
            "secrets.jwt_secret",
            settings.secrets.jwt_secret,
            sensitive_keys={"secrets.jwt_secret"},
            reveal_sensitive=False,
        )
        == "test...cret (len=15)"
    )
    assert (
        _config_echo_value(
            "db.host",
            settings.db.host,
            sensitive_keys={"secrets.jwt_secret"},
            reveal_sensitive=False,
        )
        == "dotenv-db"
    )
    assert (
        _config_echo_value(
            "secrets.jwt_secret",
            settings.secrets.jwt_secret,
            sensitive_keys={"secrets.jwt_secret"},
            reveal_sensitive=True,
        )
        == "test-jwt-secret"
    )


def test_config_echo_fully_masks_short_sensitive_values(tmp_path: Path) -> None:
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    _write_app_config(env_path, _base_env_values())
    secret_values = _secret_values()
    secret_values["JWT_SECRET"] = "abc"
    _write_env_file(secrets_path, secret_values)
    settings = _new_settings(env_path, secrets_path)

    assert (
        _config_echo_value(
            "secrets.jwt_secret",
            settings.secrets.jwt_secret,
            sensitive_keys={"secrets.jwt_secret"},
            reveal_sensitive=False,
        )
        == "*** (len=3)"
    )


def test_config_echo_reports_empty_sensitive_values(tmp_path: Path) -> None:
    env_path = tmp_path / "app.test.toml"
    secrets_path = tmp_path / ".secrets"
    secret_values = _secret_values()
    secret_values["JWT_SECRET"] = ""
    _write_app_config(env_path, _base_env_values())
    _write_env_file(secrets_path, secret_values)
    settings = _new_settings(env_path, secrets_path)

    assert (
        _config_echo_value(
            "secrets.jwt_secret",
            settings.secrets.jwt_secret,
            sensitive_keys={"secrets.jwt_secret"},
            reveal_sensitive=False,
        )
        == "<empty>"
    )
