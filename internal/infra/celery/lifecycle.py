"""Celery Worker 子进程的应用资源生命周期。"""

from internal.config import init_settings, settings
from pkg.logger import init_logger, logger
from pkg.tracing import init_tracing, shutdown_tracing


def worker_startup() -> None:
    """初始化 Worker 子进程使用的配置、日志和 tracing。"""
    try:
        init_settings()
    except Exception as exc:
        raise RuntimeError("Worker configuration initialization failed") from exc

    init_logger(
        level="INFO",
        log_format=settings.LOG_FORMAT,
        base_log_dir=(
            settings.LOG_BASE_DIR / "celery"
            if settings.LOG_BASE_DIR is not None
            else None
        ),
        write_to_file=settings.LOG_WRITE_TO_FILE,
        write_to_console=settings.LOG_WRITE_TO_CONSOLE,
    )
    init_tracing(
        enabled=settings.OTEL_TRACING_ENABLED,
        service_name=settings.OTEL_SERVICE_NAME,
        otlp_traces_endpoint=(
            str(settings.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT)
            if settings.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT is not None
            else None
        ),
        exporter_timeout_seconds=settings.OTEL_EXPORTER_OTLP_TIMEOUT_SECONDS,
    )
    logger.info(f"Celery worker initialized with APP_ENV: {settings.APP_ENV}")
    logger.info("Worker process starting: initialized basic resources.")


async def worker_shutdown() -> None:
    """在 Worker 子进程退出前清理 tracing 并刷出日志。"""
    logger.warning("Worker process stopping: cleaning up basic resources.")
    shutdown_tracing()
    await logger.complete()
