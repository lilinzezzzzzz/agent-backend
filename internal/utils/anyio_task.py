from pkg.logger import logger
from pkg.concurrency import AnyioTaskHandler
from pkg.toolkit.types import lazy_proxy

_anyio_task_manager: AnyioTaskHandler | None = None


async def init_anyio_task_handler():
    global _anyio_task_manager
    logger.info("Init anyio task manager...")
    if _anyio_task_manager is not None:
        logger.warning("Anyio task manager has been initialized.")
        return

    _anyio_task_manager = AnyioTaskHandler()
    await _anyio_task_manager.start()
    logger.success("Init anyio task manager completed.")


async def close_anyio_task_handler():
    global _anyio_task_manager
    logger.info("Stop anyio task manager...")
    if _anyio_task_manager is None:
        logger.warning("Anyio task manager has not been initialized.")
        return

    await _anyio_task_manager.shutdown()
    _anyio_task_manager = None
    logger.info("Stop anyio task manager completed.")


def _get_anyio_task_manager() -> AnyioTaskHandler:
    if _anyio_task_manager is None:
        raise RuntimeError("Anyio task manager not initialized. Call init_anyio_task_handler() first.")
    return _anyio_task_manager


anyio_task_manager = lazy_proxy(_get_anyio_task_manager)
