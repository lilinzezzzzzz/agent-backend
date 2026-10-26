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
        log_format=settings.log.format,
        base_log_dir=(
            settings.log.base_dir / "celery"
            if settings.log.base_dir is not None
            else None
        ),
        write_to_file=settings.log.write_to_file,
        write_to_console=settings.log.write_to_console,
    )
    init_tracing(
        enabled=settings.otel.tracing_enabled,
        service_name=settings.otel.service_name,
        otlp_traces_endpoint=(
            str(settings.otel.exporter_otlp_traces_endpoint)
            if settings.otel.exporter_otlp_traces_endpoint is not None
            else None
        ),
        exporter_timeout_seconds=settings.otel.exporter_otlp_timeout_seconds,
    )
    logger.info(f"Celery worker initialized with APP_ENV: {settings.app_env}")
    logger.info("Worker process starting: initialized basic resources.")


async def worker_shutdown() -> None:
    """在 Worker 子进程退出前清理 tracing 并刷出日志。"""
    logger.warning("Worker process stopping: cleaning up basic resources.")
    shutdown_tracing()
    await logger.complete()
