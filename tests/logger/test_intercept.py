import json
import logging
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from loguru import logger

from pkg.logger import InterceptHandler, LogFormat, LoggerHandler


@pytest.fixture(autouse=True)
def isolate_loguru() -> Generator[None, None, None]:
    logger.remove()
    yield
    logger.remove()


def test_intercept_preserves_message_level_and_caller() -> None:
    records: list[dict[str, Any]] = []
    logger.add(lambda message: records.append(message.record))
    source = logging.Logger("standard.example")
    source.addHandler(InterceptHandler(logger))

    source_lineno = sys._getframe().f_lineno + 1
    source.warning("value=%s", "{literal}")

    assert len(records) == 1
    record = records[0]
    assert record["message"] == "value={literal}"
    assert record["level"].name == "WARNING"
    assert record["name"] == __name__
    assert record["function"] == "test_intercept_preserves_message_level_and_caller"
    assert record["line"] == source_lineno
    assert record["extra"]["logger_name"] == "standard.example"


def test_intercept_accepts_custom_standard_logging_level() -> None:
    records: list[dict[str, Any]] = []
    logger.add(lambda message: records.append(message.record))
    source = logging.Logger("standard.custom")
    source.addHandler(InterceptHandler(logger))

    source.log(37, "custom level")

    assert len(records) == 1
    assert records[0]["level"].no == 37


def test_intercept_preserves_exception_in_single_json_line(tmp_path: Path) -> None:
    LoggerHandler(
        base_log_dir=tmp_path,
        log_format=LogFormat.JSON,
        enqueue=False,
    ).setup(write_to_file=True, write_to_console=False)
    source = logging.Logger("standard.exception")
    source.addHandler(InterceptHandler(logger))

    try:
        raise ValueError("example failure")
    except ValueError:
        source.exception("operation %s", "failed")

    records = [
        json.loads(line) for line in (tmp_path / "app.log").read_text().splitlines()
    ]
    errors = [
        entry for entry in records if entry["record"]["message"] == "operation failed"
    ]
    assert len(errors) == 1
    assert errors[0]["record"]["level"]["name"] == "ERROR"
    assert errors[0]["record"]["exception"] == {
        "type": "ValueError",
        "value": "example failure",
        "traceback": True,
    }
    assert "raise ValueError" in errors[0]["text"]


@pytest.mark.parametrize("enqueue", [False, True])
def test_intercept_respects_sink_level(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    enqueue: bool,
) -> None:
    LoggerHandler(
        level="WARNING",
        base_log_dir=tmp_path,
        log_format=LogFormat.JSON,
        enqueue=enqueue,
    ).setup(write_to_file=True, write_to_console=True)
    source = logging.Logger("standard.verbose", level=logging.DEBUG)
    source.addHandler(InterceptHandler(logger))

    source.debug("standard debug")
    source.warning("standard warning")
    logger.info("application info")
    logger.warning("application warning")
    logger.complete()

    records = [
        json.loads(line) for line in (tmp_path / "app.log").read_text().splitlines()
    ]
    assert [entry["record"]["message"] for entry in records] == [
        "standard warning",
        "application warning",
    ]
    assert records[0]["record"]["extra"]["logger_name"] == "standard.verbose"
    assert records == [
        json.loads(line) for line in capsys.readouterr().err.splitlines()
    ]
