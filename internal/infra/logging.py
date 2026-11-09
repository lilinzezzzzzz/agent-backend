"""Uvicorn 日志与应用日志输出的装配。"""

from internal.config import init_settings
from pkg.logger import init_logger, logger
from pkg.logger.handler import LoggerHandler
from pkg.logger.intercept import InterceptHandler


def create_uvicorn_log_handler() -> InterceptHandler:
    """供 Uvicorn dictConfig 在主进程和 spawn 子进程中显式创建 handler。"""
    log_settings = init_settings().log
    # 父进程没有应用 lifespan；启动阶段使用同步输出，避免创建无人管理的日志队列。
    # API lifespan 中的 init_logger() 随后用同一组配置接管输出并启用队列。
    logger = LoggerHandler(
        log_format=log_settings.format,
        base_log_dir=log_settings.base_dir,
        enqueue=False,
    ).setup(
        write_to_file=log_settings.write_to_file,
        write_to_console=log_settings.write_to_console,
    )
    return InterceptHandler(logger)


async def complete_api_logging() -> None:
    """刷出应用日志，并为 lifespan 结束后的 Uvicorn 日志恢复同步输出。"""
    try:
        await logger.complete()
    finally:
        # Uvicorn 在 lifespan 结束后恢复退出信号，队列此时可能来不及消费尾部日志。
        log_settings = init_settings().log
        init_logger(
            log_format=log_settings.format,
            base_log_dir=log_settings.base_dir,
            enqueue=False,
            write_to_file=log_settings.write_to_file,
            write_to_console=log_settings.write_to_console,
        )
