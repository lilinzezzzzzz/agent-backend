import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from loguru import logger as loguru_logger

from pkg.logger import LogFormat, LoggerHandler

# 全局变量用于存储测试期间的 manager 实例
_test_manager: LoggerHandler | None = None


@pytest.fixture
def setup_logging(tmp_path):
    """
    Fixture: 初始化测试环境
    """
    global _test_manager
    base_log_dir = tmp_path / "logs"
    base_log_dir.mkdir(parents=True, exist_ok=True)  # 确保目录存在

    # 创建新的 LoggerHandler 实例
    _test_manager = LoggerHandler(
        base_log_dir=base_log_dir,
    )
    _test_manager.setup(write_to_file=True, write_to_console=False)

    print(f"\n---> 当前测试日志路径: {base_log_dir}")

    yield base_log_dir

    # 清理
    loguru_logger.remove()
    _test_manager = None


def find_json_log(file_path, target_message: str) -> dict[str, Any] | None:
    """JSON 日志查找辅助函数"""
    if not file_path.exists():
        return None
    content = file_path.read_text(encoding="utf-8")
    for line in content.strip().split("\n"):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            # 5. 结构调整：新格式没有外层的 "record" 包装，直接是扁平字典
            if data.get("message") == target_message:
                return data
        except json.JSONDecodeError:
            continue
    return None


def find_text_log(file_path, target_message: str) -> bool:
    """文本日志查找辅助函数 (用于 System Log)"""
    if not file_path.exists():
        return False
    content = file_path.read_text(encoding="utf-8")
    return target_message in content


def test_default_logging(setup_logging):
    """
    测试默认日志 (Default Log)
    注意：根据最新代码，Default Log 固定为文本格式，不是 JSON。
    """
    base_log_dir = setup_logging
    msg = "Default logger start sequence"

    # 使用测试 manager 的 logger
    assert _test_manager is not None
    _test_manager._logger.info(msg)
    loguru_logger.complete()

    files = list(base_log_dir.glob("*.log"))
    assert len(files) == 1
    expected = files[0]
    assert expected.name == "app.log"

    # 验证文本内容
    assert find_text_log(expected, msg), "未在文本日志中找到目标消息"


def test_file_logging_only_appends_to_fixed_file(tmp_path):
    base_log_dir = tmp_path / "logs"
    archive = base_log_dir / "app.2026-07-23.log"
    base_log_dir.mkdir(parents=True)
    archive.write_text("managed by external rotation\n", encoding="utf-8")

    manager = LoggerHandler(base_log_dir=base_log_dir, enqueue=False)
    logger = manager.setup(write_to_file=True, write_to_console=False)
    logger.info("first entry")
    logger.info("second entry")

    assert sorted(path.name for path in base_log_dir.iterdir()) == [
        "app.2026-07-23.log",
        "app.log",
    ]
    assert archive.read_text(encoding="utf-8") == "managed by external rotation\n"
    assert find_text_log(base_log_dir / "app.log", "first entry")
    assert find_text_log(base_log_dir / "app.log", "second entry")


def test_file_logging_can_be_disabled_without_creating_directory(tmp_path):
    base_log_dir = tmp_path / "logs"
    manager = LoggerHandler(base_log_dir=base_log_dir, enqueue=False)

    manager.setup(write_to_file=False, write_to_console=False)

    assert not base_log_dir.exists()


def test_file_logging_requires_base_log_dir():
    manager = LoggerHandler(enqueue=False)

    with pytest.raises(
        ValueError, match="base_log_dir is required when write_to_file=True"
    ):
        manager.setup(write_to_file=True, write_to_console=False)


def test_default_setup_only_writes_to_console(tmp_path):
    base_log_dir = tmp_path / "logs"
    manager = LoggerHandler(base_log_dir=base_log_dir, enqueue=False)

    manager.setup()

    assert not base_log_dir.exists()


def test_setup_disables_diagnose_for_all_sinks(tmp_path):
    manager = LoggerHandler(base_log_dir=tmp_path / "logs", enqueue=False)

    with patch.object(manager._logger, "add", wraps=manager._logger.add) as add:
        manager.setup(write_to_file=True, write_to_console=True)

    assert len(add.call_args_list) == 2
    assert all(call.kwargs["diagnose"] is False for call in add.call_args_list)


def test_timezone_accepts_string_zoneinfo_and_datetime_utc():
    assert LoggerHandler(timezone="Asia/Shanghai").timezone == ZoneInfo("Asia/Shanghai")
    assert LoggerHandler(timezone=ZoneInfo("America/New_York")).timezone == ZoneInfo(
        "America/New_York"
    )
    assert LoggerHandler(timezone=UTC).timezone == ZoneInfo("UTC")


def test_text_formatter_shows_trace_id_without_span_id():
    handler = LoggerHandler()

    record = {
        "time": datetime(2026, 4, 20, 2, 51, 41, 837000, tzinfo=UTC),
        "level": type("Level", (), {"name": "ERROR"})(),
        "name": "pkg.tracing.span",
        "function": "__aexit__",
        "line": 211,
        "message": "request failed",
        "extra": {
            "trace_id": "019da8cd058b76ed8a4a52141c1c6b38",
            "span_id": "00f067aa0ba902b7",
            "json_content": {
                "elapsed_ms": 0.554,
                "error_type": "AppException",
            },
        },
    }

    formatted = handler._text_formatter(record)

    assert formatted == (
        "{extra[_formatted_time]} | "
        "{level: <8} | "
        "{name}:{function}:{line} | "
        "{extra[_formatted_trace_id]} | "
        "{message} | {extra[_text_json]}\n{exception}"
    )
    assert record["extra"]["_formatted_time"] == "2026-04-20T02:51:41.837Z"
    assert record["extra"]["_formatted_trace_id"] == "019da8cd058b76ed8a4a52141c1c6b38"


def test_text_formatter_keeps_braces_in_dynamic_values():
    handler = LoggerHandler()

    record = {
        "time": datetime(2026, 4, 20, 2, 51, 41, 837000, tzinfo=UTC),
        "level": type("Level", (), {"name": "INFO"})(),
        "name": "pkg.tracing.span",
        "function": "__aenter__",
        "line": 181,
        "message": "message-{raw}",
        "extra": {
            "trace_id": "trace-{raw}",
            "json_content": None,
        },
    }

    formatted = handler._text_formatter(record)

    assert formatted == (
        "{extra[_formatted_time]} | "
        "{level: <8} | "
        "{name}:{function}:{line} | "
        "{extra[_formatted_trace_id]} | "
        "{message}\n{exception}"
    )
    assert record["extra"]["_formatted_trace_id"] == "trace-{raw}"


def test_json_formatter_excludes_span_id():
    handler = LoggerHandler()
    record = {
        "time": datetime(2026, 4, 20, 2, 51, 41, 837000, tzinfo=UTC),
        "level": type("Level", (), {"name": "INFO"})(),
        "name": "pkg.logger.handler",
        "function": "setup",
        "line": 1,
        "message": "initialized",
        "extra": {
            "trace_id": "019da8cd058b76ed8a4a52141c1c6b38",
            "span_id": "00f067aa0ba902b7",
            "request_id": "request-123",
            "json_content": None,
        },
    }

    formatted = handler._json_formatter(record)
    serialized = json.loads(record["extra"]["_json_out"])

    assert formatted == "{extra[_json_out]}\n"
    assert serialized["trace_id"] == "019da8cd058b76ed8a4a52141c1c6b38"
    assert serialized["timestamp"] == "2026-04-20T02:51:41.837Z"
    assert serialized["severity_text"] == "INFO"
    assert serialized["message"] == "initialized"
    assert serialized["code"] == {
        "namespace": "pkg.logger.handler",
        "function": "setup",
        "lineno": 1,
    }
    assert serialized["attributes"] == {"request_id": "request-123"}
    assert "time" not in serialized
    assert "level" not in serialized
    assert "location" not in serialized
    assert "text" not in serialized
    assert "span_id" not in serialized


def test_json_logging_keeps_exception_in_single_json_line(tmp_path):
    base_log_dir = tmp_path / "logs"
    manager = LoggerHandler(
        base_log_dir=base_log_dir,
        log_format=LogFormat.JSON,
        enqueue=False,
    )
    logger = manager.setup(write_to_file=True, write_to_console=False)

    try:
        raise ValueError("invalid value")
    except ValueError:
        logger.exception("operation failed")

    lines = (base_log_dir / "app.log").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    exception_record = next(
        record for record in records if record["message"] == "operation failed"
    )

    assert all(line.strip() for line in lines)
    assert exception_record["exception"]["type"] == "ValueError"
    assert exception_record["exception"]["message"] == "invalid value"
    assert "ValueError: invalid value" in exception_record["exception"]["stacktrace"]


def test_text_logging_keeps_exception_traceback(tmp_path):
    base_log_dir = tmp_path / "logs"
    manager = LoggerHandler(base_log_dir=base_log_dir, enqueue=False)
    logger = manager.setup(write_to_file=True, write_to_console=False)

    try:
        raise RuntimeError("operation failed")
    except RuntimeError:
        logger.exception("unexpected error")

    content = (base_log_dir / "app.log").read_text(encoding="utf-8")
    assert "unexpected error" in content
    assert "Traceback (most recent call last)" in content
    assert "RuntimeError: operation failed" in content
