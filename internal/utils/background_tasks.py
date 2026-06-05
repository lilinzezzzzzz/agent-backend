from pkg.concurrency import AnyioTaskHandler
from pkg.logger import logger
from pkg.toolkit.types import lazy_proxy

_background_task_manager: AnyioTaskHandler | None = None


async def init_background_task_manager() -> None:
    global _background_task_manager
    logger.info("Init background task manager...")
    if _background_task_manager is not None:
        logger.warning("Background task manager has been initialized.")
        return

    _background_task_manager = AnyioTaskHandler()
    await _background_task_manager.start()
    logger.success("Init background task manager completed.")


async def close_background_task_manager() -> None:
    global _background_task_manager
    logger.info("Stop background task manager...")
    if _background_task_manager is None:
        logger.warning("Background task manager has not been initialized.")
        return

    await _background_task_manager.shutdown()
    _background_task_manager = None
    logger.info("Stop background task manager completed.")


def _get_background_task_manager() -> AnyioTaskHandler:
    if _background_task_manager is None:
        raise RuntimeError("Background task manager not initialized. Call init_background_task_manager() first.")
    return _background_task_manager


background_task_manager = lazy_proxy(_get_background_task_manager)
