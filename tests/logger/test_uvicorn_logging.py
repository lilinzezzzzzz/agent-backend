import json
import logging
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger
from uvicorn import Config

import internal.infra.logging as logging_module
import pkg.logger as logger_module
from internal.config import LogSettings
from pkg.logger import LogFormat, init_logger

LOG_CONFIG = str(
    Path(__file__).resolve().parents[2] / "entrypoints" / "uvicorn_logging.json"
)


@pytest.fixture(autouse=True)
def isolate_logging(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    sources = [
        logging.getLogger(name)
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi")
    ]
    states = [
        (source.handlers[:], source.level, source.propagate, source.disabled)
        for source in sources
    ]
    monkeypatch.setattr(logger_module, "_logger", None)
    monkeypatch.setattr(logger_module, "_logger_manager", None)
    logger.remove()
    yield
    logger.remove()
    for source, state in zip(sources, states, strict=True):
        handlers, level, propagate, disabled = state
        source.handlers = handlers
        source.setLevel(level)
        source.propagate = propagate
        source.disabled = disabled


@pytest.fixture
def log_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> LogSettings:
    settings = LogSettings(
        format=LogFormat.JSON,
        base_dir=tmp_path / "logs",
        write_to_file=True,
        write_to_console=False,
    )
    monkeypatch.setattr(
        logging_module,
        "init_settings",
        lambda: SimpleNamespace(log=settings),
    )
    return settings


def _read_records(log_settings: LogSettings) -> list[dict[str, Any]]:
    assert log_settings.base_dir is not None
    return [
        json.loads(line)["record"]
        for line in (log_settings.base_dir / "app.log").read_text().splitlines()
    ]


@pytest.mark.parametrize("source_name", ["uvicorn", "uvicorn.error", "uvicorn.access"])
def test_uvicorn_bootstrap_uses_application_file_without_duplicates(
    source_name: str,
    log_settings: LogSettings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root_handlers = logging.getLogger().handlers[:]
    config = Config("unused:app", log_config=LOG_CONFIG)
    config.configure_logging()

    logging.getLogger(source_name).info("bootstrap %s", source_name)

    records = [
        record
        for record in _read_records(log_settings)
        if record["message"] == f"bootstrap {source_name}"
    ]
    assert len(records) == 1
    assert records[0]["extra"]["logger_name"] == source_name
    assert logging.getLogger().handlers == root_handlers
    assert capsys.readouterr().err == ""
    assert logger_module._logger_manager is None


def test_uvicorn_bootstrap_can_use_console_only(
    log_settings: LogSettings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    log_settings.write_to_file = False
    log_settings.write_to_console = True
    Config("unused:app", log_config=LOG_CONFIG)

    logging.getLogger("uvicorn.error").info("console bootstrap")

    records = [
        json.loads(line)["record"] for line in capsys.readouterr().err.splitlines()
    ]
    assert sum(record["message"] == "console bootstrap" for record in records) == 1
    assert log_settings.base_dir is not None
    assert not log_settings.base_dir.exists()


def test_uvicorn_keeps_forwarding_after_lifespan_logger_initialization(
    log_settings: LogSettings,
) -> None:
    Config("unused:app", log_config=LOG_CONFIG)
    source = logging.getLogger("uvicorn.error")
    source.info("before lifespan")

    init_logger(
        log_format=log_settings.format,
        base_log_dir=log_settings.base_dir,
        write_to_file=log_settings.write_to_file,
        write_to_console=log_settings.write_to_console,
    )
    source.info("after lifespan")
    logger.complete()

    messages = [record["message"] for record in _read_records(log_settings)]
    assert messages.count("before lifespan") == 1
    assert messages.count("after lifespan") == 1


def test_uvicorn_access_log_can_be_disabled(log_settings: LogSettings) -> None:
    Config("unused:app", log_config=LOG_CONFIG, access_log=False)

    logging.getLogger("uvicorn.access").info("disabled access")
    logging.getLogger("uvicorn.error").info("server event")

    messages = [record["message"] for record in _read_records(log_settings)]
    assert "disabled access" not in messages
    assert messages.count("server event") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("completion_fails", [False, True])
async def test_uvicorn_shutdown_logs_are_written_synchronously(
    log_settings: LogSettings,
    monkeypatch: pytest.MonkeyPatch,
    completion_fails: bool,
) -> None:
    Config("unused:app", log_config=LOG_CONFIG)
    init_logger(
        log_format=log_settings.format,
        base_log_dir=log_settings.base_dir,
        write_to_file=log_settings.write_to_file,
        write_to_console=log_settings.write_to_console,
    )
    logging.getLogger("uvicorn.error").info("before shutdown")

    if completion_fails:
        monkeypatch.setattr(
            logging_module,
            "logger",
            MagicMock(complete=AsyncMock(side_effect=RuntimeError("flush failed"))),
        )
        with pytest.raises(RuntimeError, match="flush failed"):
            await logging_module.complete_api_logging()
    else:
        await logging_module.complete_api_logging()

    logging.getLogger("uvicorn.error").info("after shutdown")

    # 不再调用 complete；lifespan 结束后的日志必须在退出信号到来前同步写出。
    messages = [record["message"] for record in _read_records(log_settings)]
    assert messages.count("before shutdown") == 1
    assert messages.count("after shutdown") == 1
    assert logger_module.get_logger_manager().enqueue is False


def test_uvicorn_log_level_still_filters_server_messages(
    log_settings: LogSettings,
) -> None:
    Config("unused:app", log_config=LOG_CONFIG, log_level="warning")

    logging.getLogger("uvicorn.error").info("filtered info")
    logging.getLogger("uvicorn.error").warning("server warning")

    messages = [record["message"] for record in _read_records(log_settings)]
    assert "filtered info" not in messages
    assert messages.count("server warning") == 1


@pytest.mark.parametrize("log_level", ["debug", "trace"])
def test_uvicorn_verbose_logs_respect_sink_level(
    log_settings: LogSettings,
    log_level: str,
) -> None:
    Config("unused:app", log_config=LOG_CONFIG, log_level=log_level)

    logging.getLogger("uvicorn.error").debug("server debug")
    logging.getLogger("uvicorn.asgi").log(5, "server trace")
    logging.getLogger("uvicorn.error").info("server info")
    logger.debug("application debug")

    records = _read_records(log_settings)
    messages = [record["message"] for record in records]
    assert "server debug" not in messages
    assert "server trace" not in messages
    assert messages.count("server info") == 1
    assert "application debug" not in messages
