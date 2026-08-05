from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

import internal.app as app_module
import internal.infra.celery.lifecycle as celery_lifecycle


@pytest.mark.asyncio
async def test_fastapi_shutdown_waits_for_enqueued_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "init_settings", MagicMock())
    monkeypatch.setattr(app_module, "init_logger", MagicMock())
    monkeypatch.setattr(app_module, "init_tracing", MagicMock())
    monkeypatch.setattr(app_module, "init_async_db", MagicMock())
    monkeypatch.setattr(app_module, "init_async_redis", MagicMock())
    monkeypatch.setattr(app_module, "init_signature_auth_handler", MagicMock())
    monkeypatch.setattr(app_module, "init_snowflake_id_generator", MagicMock())
    monkeypatch.setattr(app_module, "init_background_task_manager", AsyncMock())
    monkeypatch.setattr(app_module, "close_async_db", AsyncMock())
    monkeypatch.setattr(app_module, "close_async_redis", AsyncMock())
    monkeypatch.setattr(app_module, "close_background_task_manager", AsyncMock())
    monkeypatch.setattr(app_module, "shutdown_tracing", MagicMock())
    complete = AsyncMock()
    logger = MagicMock()
    logger.complete = complete
    monkeypatch.setattr(app_module, "logger", logger)

    async with app_module.lifespan(FastAPI()):
        pass

    complete.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_celery_worker_shutdown_waits_for_enqueued_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(celery_lifecycle, "shutdown_tracing", MagicMock())
    complete = AsyncMock()
    logger = MagicMock()
    logger.complete = complete
    monkeypatch.setattr(celery_lifecycle, "logger", logger)

    await celery_lifecycle.worker_shutdown()

    complete.assert_awaited_once_with()
