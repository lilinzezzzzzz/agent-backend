import sys
from datetime import timezone as dt_timezone
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import loguru

from pkg import request_context as context
from pkg.toolkit.json import orjson_dumps
from pkg.toolkit.timer import format_iso_datetime

_ACTIVE_LOG_FILE_NAME = "app.log"

TimezoneType = str | ZoneInfo | dt_timezone


class LogFormat(StrEnum):
    """日志格式枚举"""

    JSON = "json"
    TEXT = "text"


class LoggerHandler:
    """
    日志管理器
    配置在实例化 (__init__) 时传入，并在 setup() 时生效。
    """

    def __init__(
        self,
        *,
        level: str = "INFO",
        base_log_dir: Path | None = None,
        timezone: TimezoneType = "UTC",
        enqueue: bool = True,
        log_format: LogFormat = LogFormat.TEXT,
    ) -> None:
        """
        构造函数：接收所有配置参数并存储为实例属性。

        :param level: 日志等级 (e.g., "INFO", "DEBUG")
        :param base_log_dir: 日志存放的根目录；开启文件输出时必须提供
        :param timezone: 日志时区，支持时区字符串（如 "UTC", "Asia/Shanghai"）、ZoneInfo 对象或 datetime.timezone（如 datetime.UTC），默认 "UTC"
        :param enqueue: 是否使用异步队列写入
        :param log_format: 日志格式 (LogFormat.JSON 或 LogFormat.TEXT，默认 LogFormat.TEXT)
        """

        self._logger = loguru.logger

        # --- 配置属性 ---
        self.level = level
        self.base_log_dir = base_log_dir
        self.enqueue = enqueue
        self.log_format = log_format

        # --- 根据 log_format 确定格式化器 ---
        self.serialize = self.log_format == LogFormat.JSON
        self.console_format = "{message}" if self.serialize else self._console_formatter
        self.file_format = "{message}" if self.serialize else self._text_formatter
        self.colorize = not self.serialize

        # --- 时区处理 ---
        self.timezone = self._normalize_timezone(timezone)

    def _normalize_timezone(self, timezone: TimezoneType) -> ZoneInfo:
        """
        将不同类型的时区参数统一转换为 ZoneInfo 对象。

        Args:
            timezone: 时区参数，支持字符串、ZoneInfo 或 datetime.timezone

        Returns:
            ZoneInfo 对象

        Raises:
            ValueError: 不支持的时区类型（如非UTC的datetime.timezone）
            TypeError: 无效的参数类型
        """
        if isinstance(timezone, str):
            return ZoneInfo(timezone)
        elif isinstance(timezone, ZoneInfo):
            return timezone
        elif isinstance(timezone, dt_timezone):
            # datetime.timezone 类型（如 datetime.UTC）
            tz_name = str(timezone)
            if tz_name == "UTC":
                return ZoneInfo("UTC")
            else:
                raise ValueError(
                    f"Unsupported timezone: {timezone}. "
                    f"Use ZoneInfo for non-UTC timezones, e.g., ZoneInfo('Asia/Shanghai')."
                )
        else:
            raise TypeError(
                f"timezone must be str, ZoneInfo, or datetime.timezone, got {type(timezone).__name__}"
            )

    def _get_log_dir(self) -> Path:
        """
        获取显式配置的文件日志目录。

        Returns:
            日志目录路径
        """
        if self.base_log_dir is None:
            raise ValueError("base_log_dir is required when write_to_file=True")
        return self.base_log_dir

    def setup(
        self, *, write_to_file: bool = False, write_to_console: bool = True
    ) -> "loguru.Logger":
        """
        应用配置并初始化系统日志。
        注意：setup 不再接收配置参数，而是使用 __init__ 中保存的属性。
        """
        self._logger.remove()

        # 1. 准备基础配置
        config_params: dict[str, Any] = {
            "extra": {
                "json_content": None,
            },
        }

        # 2. 根据时区配置挂载 patcher，统一日志内容中的时间与关联字段
        config_params["patcher"] = self._make_record_patcher()

        self._logger.configure(**config_params)

        # 3. Console 输出
        if write_to_console:
            self._logger.add(
                sink=sys.stderr,
                level=self.level,
                enqueue=self.enqueue,
                serialize=self.serialize,
                colorize=self.colorize,
                diagnose=False,
                format=self.console_format,
            )

        # 4. File 输出 (System Log)
        if write_to_file:
            log_dir = self._get_log_dir()
            self._ensure_dir(log_dir)
            # 应用只向固定文件追加日志，不负责 rotation、retention 和 compression。
            # 多 Worker 进程不能安全地独立轮转同一文件；日志生命周期应由
            # Docker logging driver、Linux logrotate 或 Kubernetes 日志平台统一管理。
            sink_path = log_dir / _ACTIVE_LOG_FILE_NAME

            self._logger.add(
                sink=sink_path,
                level=self.level,
                enqueue=self.enqueue,
                serialize=self.serialize,
                diagnose=False,
                format=self.file_format,
            )

        tz_name = (
            self.timezone.key if hasattr(self.timezone, "key") else str(self.timezone)
        )
        self._logger.info(
            f"Logger initialized. Timezone: {tz_name} | Format: {self.log_format} | Level: {self.level}"
        )
        return self._logger

    # --- 时间格式化 ---

    def _format_record_time(self, record: Any) -> str:
        """
        格式化日志记录的时间戳。

        注意：_make_record_patcher 已经将 record["time"] 转换为目标时区。
        此方法直接格式化时间，统一使用 ISO 8601 格式。

        Args:
            record: loguru 日志记录对象

        Returns:
            ISO 8601 格式的时间字符串
        """
        record_time = record["time"]

        # 处理 naive datetime（loguru 默认会带时区，但为了健壮性做处理）
        if record_time.tzinfo is None:
            # 为 naive datetime 添加时区信息
            record_time = record_time.replace(tzinfo=self.timezone)

        return format_iso_datetime(record_time)

    # --- 格式化器 ---

    def _console_formatter(self, record: Any) -> str:
        """控制台文本格式化器，按需追加 json_content。"""
        self._populate_text_format_extra(record)

        fmt = (
            "<green>{extra[_formatted_time]}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<magenta>{extra[_formatted_trace_id]}</magenta> | "
            "<level>{message}</level>"
        )

        # 检查 json_content 是否存在且不为 None
        json_content = record["extra"].get("json_content")
        if json_content is not None:
            serialized = orjson_dumps(json_content, default=str)
            record["extra"]["_console_json"] = serialized
            fmt += " | <cyan>{extra[_console_json]}</cyan>"
        return fmt + "\n{exception}"

    def _text_formatter(self, record: Any) -> str:
        """
        TEXT 动态格式化器 (log_format='text' 时使用)
        """
        self._populate_text_format_extra(record)

        fmt = (
            "{extra[_formatted_time]} | "
            "{level: <8} | "
            "{name}:{function}:{line} | "
            "{extra[_formatted_trace_id]} | "
            "{message}"
        )

        # 检查并追加 json_content
        json_content = record["extra"].get("json_content")
        if json_content is not None:
            # 序列化为 JSON 字符串并存入 extra，避免直接输出 dict
            serialized = orjson_dumps(json_content, default=str)
            record["extra"]["_text_json"] = serialized
            fmt += " | {extra[_text_json]}"

        return fmt + "\n{exception}"

    # --- 辅助方法 ---
    def _make_record_patcher(self):
        """
        创建日志记录补丁函数。

        此补丁在日志入队前执行：
        1. 将 record["time"] 转换为配置的目标时区
        2. 注入标准 trace_id 字段

        Returns:
            日志记录补丁函数
        """
        target_tz = self.timezone

        def patcher(record: Any):
            record["time"] = record["time"].astimezone(target_tz)
            record["extra"]["trace_id"] = self._safe_get_trace_id()

        return patcher

    @staticmethod
    def _ensure_dir(path: Path):
        """确保目录存在，如果父目录不存在则自动创建"""
        path.mkdir(parents=True, exist_ok=True)

    def _populate_text_format_extra(self, record: Any) -> None:
        record["extra"]["_formatted_time"] = self._format_record_time(record)
        record["extra"]["_formatted_trace_id"] = record["extra"].get("trace_id") or "-"

    @classmethod
    def _safe_get_trace_id(cls) -> str:
        """
        获取 trace_id，仅从 context.get_trace_id() 读取，未设置时返回 "-"

        Returns:
            trace_id 字符串
        """
        try:
            return context.get_trace_id()
        except LookupError:
            return "-"
