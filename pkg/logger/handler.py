import sys
from datetime import UTC, time, timedelta
from datetime import timezone as dt_timezone
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import loguru
from opentelemetry import trace

from pkg import request_context as context
from pkg.toolkit.json import orjson_dumps
from pkg.toolkit.timer import format_iso_datetime

# 默认日志目录
_DEFAULT_BASE_LOG_DIR = Path("/tmp/fastapi_logs")

# 类型别名
RotationType = str | int | time | timedelta
RetentionType = str | int | timedelta
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
        rotation: RotationType = time(0, 0, 0, tzinfo=UTC),
        retention: RetentionType = timedelta(days=30),
        compression: str | None = None,
        timezone: TimezoneType = "UTC",
        enqueue: bool = True,
        log_format: LogFormat = LogFormat.TEXT,
    ):
        """
        构造函数：接收所有配置参数并存储为实例属性。

        :param level: 日志等级 (e.g., "INFO", "DEBUG")
        :param base_log_dir: 日志存放的根目录，默认为当前文件父级路径下的 logs 目录
        :param rotation: 轮转策略 (默认: 每天 00:00, UTC时间)
        :param retention: 保留策略 (默认: 30天)
        :param compression: 压缩格式 (e.g., "zip")
        :param timezone: 日志时区，支持时区字符串（如 "UTC", "Asia/Shanghai"）、ZoneInfo 对象或 datetime.timezone（如 datetime.UTC），默认 "UTC"
        :param enqueue: 是否使用多进程安全的队列写入
        :param log_format: 日志格式 (LogFormat.JSON 或 LogFormat.TEXT，默认 LogFormat.TEXT)
        """

        self._logger = loguru.logger

        # --- 配置属性 ---
        self.level = level
        self.base_log_dir = base_log_dir or _DEFAULT_BASE_LOG_DIR
        self.retention = retention
        self.compression = compression
        self.enqueue = enqueue
        self.log_format = log_format

        # --- 根据 log_format 确定格式化器 ---
        is_json = self.log_format == LogFormat.JSON
        self.console_format = (
            self._json_formatter if is_json else self._console_formatter
        )
        self.file_format = self._json_formatter if is_json else self._text_formatter
        self.colorize = not is_json

        # --- 时区处理 ---
        self.timezone = self._normalize_timezone(timezone)

        # --- 轮转策略处理 ---
        self.rotation = self._normalize_rotation(rotation)

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

    def _normalize_rotation(self, rotation: RotationType) -> RotationType:
        """
        规范化轮转策略，处理 time 类型的时区问题。

        对于 time 类型的 rotation：
        - 无时区：自动使用 timezone 的时区
        - 有时区：必须与 timezone 一致

        Args:
            rotation: 轮转策略参数

        Returns:
            规范化后的轮转策略

        Raises:
            ValueError: rotation 时区与 timezone 不一致
        """
        if not isinstance(rotation, time):
            return rotation

        if rotation.tzinfo is None:
            # 无时区，自动使用 timezone 的时区
            return rotation.replace(tzinfo=self.timezone)

        # 有时区，检查是否与 timezone 一致
        rotation_tz_name = getattr(rotation.tzinfo, "key", None) or str(rotation.tzinfo)
        timezone_name = self.timezone.key or str(self.timezone)
        if rotation_tz_name != timezone_name:
            raise ValueError(
                f"rotation timezone ({rotation_tz_name}) must match timezone ({timezone_name}). "
                f"Use time({rotation.hour}, {rotation.minute}, {rotation.second}, tzinfo=self.timezone) "
                f"or omit tzinfo to auto-use timezone."
            )
        return rotation

    def _get_log_dir(self) -> Path:
        """
        获取默认系统日志目录。

        Returns:
            日志目录路径
        """
        return self.base_log_dir

    def setup(
        self, *, write_to_file: bool = True, write_to_console: bool = True
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
                "trace_id": "-",
                "span_id": None,
            },
        }

        # 2. 根据时区配置挂载 patcher
        # 注意：patcher 影响日志文件名 {time:YYYY-MM-DD}.log 的时区
        # 保持文件名时间与日志内容时间一致
        config_params["patcher"] = self._make_record_patcher()

        self._logger.configure(**config_params)

        # 3. Console 输出
        if write_to_console:
            self._logger.add(
                sink=sys.stderr,
                level=self.level,
                enqueue=self.enqueue,
                colorize=self.colorize,
                diagnose=True,
                format=self.console_format,
            )

        # 4. File 输出 (System Log)
        if write_to_file:
            log_dir = self._get_log_dir()
            self._ensure_dir(log_dir)
            sink_path = log_dir / "{time:YYYY-MM-DD}.log"

            self._logger.add(
                sink=sink_path,
                level=self.level,
                rotation=self.rotation,
                retention=self.retention,
                compression=self.compression,
                enqueue=self.enqueue,
                format=self.file_format,
            )

        tz_name = (
            self.timezone.key if hasattr(self.timezone, "key") else str(self.timezone)
        )
        self._logger.info(
            f"Logger initialized. Timezone: {tz_name} | Format: {self.log_format} | Rotation: {self.rotation} | Level: {self.level}"
        )
        return self._logger

    # --- 时间格式化 ---

    def _format_record_time(self, record: Any) -> str:
        """
        格式化日志记录的时间戳。

        注意：_make_timezone_patcher 已经将 record["time"] 转换为目标时区。
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
        """控制台格式化器，仅输出纯文本，不包含 json_content"""
        self._populate_text_format_extra(record)

        fmt = (
            "<green>{extra[_formatted_time]}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<magenta>{extra[_formatted_trace_id]}</magenta> | "
            "<magenta>{extra[_formatted_span_id]}</magenta> | "
            "<level>{message}</level>"
        )

        # 检查 json_content 是否存在且不为 None
        json_content = record["extra"].get("json_content")
        if json_content is not None:
            serialized = orjson_dumps(json_content, default=str)
            record["extra"]["_console_json"] = serialized
            fmt += " | <cyan>{extra[_console_json]}</cyan>"
        return fmt + "\n"

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
            "{extra[_formatted_span_id]} | "
            "{message}"
        )

        # 检查并追加 json_content
        json_content = record["extra"].get("json_content")
        if json_content is not None:
            # 序列化为 JSON 字符串并存入 extra，避免直接输出 dict
            serialized = orjson_dumps(json_content, default=str)
            record["extra"]["_text_json"] = serialized
            fmt += " | {extra[_text_json]}"

        return fmt + "\n"

    def _json_formatter(self, record: Any) -> str:
        """JSON Lines 格式化器 (log_format='json' 时使用)"""
        formatted_time = self._format_record_time(record)

        extra_data = record["extra"].copy()
        json_content = extra_data.pop("json_content", None)
        extra_data.pop("_console_json", None)
        extra_data.pop("_json_out", None)
        extra_data.pop("_text_json", None)
        extra_data.pop("_formatted_time", None)
        extra_data.pop("_formatted_trace_id", None)
        extra_data.pop("_formatted_span_id", None)

        trace_id = extra_data.pop("trace_id", "-") or "-"
        span_id = extra_data.pop("span_id", None)

        if not isinstance(json_content, (dict, list, str, type(None))):
            raise TypeError(
                f"json_content must be types or None. Got {type(json_content)}"
            )

        log_record = {
            "time": formatted_time,
            "level": record["level"].name,
            "trace_id": trace_id,
            "span_id": span_id,
            "location": f"{record['name']}.{record['function']}:{record['line']}",
            "text": "",
            "message": record["message"],
            **extra_data,
        }

        if json_content is not None:
            log_record["json_content"] = json_content

        serialized = orjson_dumps(log_record, default=str)
        record["extra"]["_json_out"] = serialized
        return "{extra[_json_out]}\n\n"

    # --- 辅助方法 ---
    def _make_record_patcher(self):
        """
        创建日志记录补丁函数。

        此补丁在日志入队前执行：
        1. 将 record["time"] 转换为配置的目标时区
        2. 注入标准 trace_id 字段
        3. 注入当前活跃 span 的字段

        影响：
        1. 日志文件名 {time:YYYY-MM-DD}.log 的时区
        2. 保持文件名时间与日志内容时间一致

        Returns:
            日志记录补丁函数
        """
        target_tz = self.timezone

        def patcher(record: Any):
            record["time"] = record["time"].astimezone(target_tz)
            trace_id, span_id = self._get_current_trace_fields()
            record["extra"]["trace_id"] = trace_id
            record["extra"]["span_id"] = span_id

        return patcher

    @staticmethod
    def _ensure_dir(path: Path):
        """确保目录存在，如果父目录不存在则自动创建"""
        path.mkdir(parents=True, exist_ok=True)

    def _populate_text_format_extra(self, record: Any) -> None:
        record["extra"]["_formatted_time"] = self._format_record_time(record)
        record["extra"]["_formatted_trace_id"] = record["extra"].get("trace_id") or "-"
        span_id = record["extra"].get("span_id")
        record["extra"]["_formatted_span_id"] = (
            span_id if isinstance(span_id, str) and span_id else "-"
        )

    @classmethod
    def _get_current_trace_fields(cls) -> tuple[str, str | None]:
        """优先读取当前 OTel Span；无有效 Span 时回退请求关联 ID。"""

        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            return f"{span_context.trace_id:032x}", f"{span_context.span_id:016x}"
        return cls._safe_get_trace_id(), None

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
