import inspect
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from loguru import Logger


class InterceptHandler(logging.Handler):
    """将标准 logging 记录转发到注入的 Loguru logger，不配置输出目标。"""

    def __init__(self, logger: "Logger") -> None:
        super().__init__()
        self._logger = logger

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = self._logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = inspect.currentframe(), 0
        while frame:
            filename = frame.f_code.co_filename
            is_logging = filename == logging.__file__
            is_frozen_import = "importlib" in filename and "_bootstrap" in filename
            if depth > 0 and not (is_logging or is_frozen_import):
                break
            frame = frame.f_back
            depth += 1

        self._logger.bind(logger_name=record.name).opt(
            depth=depth,
            exception=record.exc_info,
        ).log(level, record.getMessage())
