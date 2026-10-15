import asyncio
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest
from fastapi import FastAPI

import internal.app as app_module
import internal.infra.celery.lifecycle as celery_lifecycle
import internal.utils.background_tasks as background_tasks


@pytest.fixture
def lifecycle_events(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []
    monkeypatch.setattr(app_module, "settings", MagicMock())
    for name in (
        "init_settings",
        "init_logger",
        "init_tracing",
        "init_async_db",
        "init_async_redis",
        "init_signature_auth_handler",
    ):
        monkeypatch.setattr(app_module, name, MagicMock())

    monkeypatch.setattr(app_module, "init_background_task_manager", AsyncMock())
    for name in (
        "close_background_task_manager",
        "close_async_redis",
        "close_async_db",
    ):

        async def close_resource(event: str = name) -> None:
            if event != "close_background_task_manager":
                await anyio.sleep(0)
            events.append(event)

        monkeypatch.setattr(app_module, name, AsyncMock(side_effect=close_resource))

    monkeypatch.setattr(
        app_module,
        "shutdown_tracing",
        MagicMock(side_effect=lambda: events.append("shutdown_tracing")),
    )

    async def complete() -> None:
        await anyio.sleep(0)
        events.append("logger.complete")

    logger = MagicMock()
    logger.complete = AsyncMock(side_effect=complete)
    monkeypatch.setattr(app_module, "logger", logger)
    return events


_CLEANUP_ORDER = [
    "close_background_task_manager",
    "close_async_redis",
    "close_async_db",
    "shutdown_tracing",
    "logger.complete",
]


@pytest.mark.asyncio
async def test_fastapi_shutdown_waits_for_enqueued_logs(
    lifecycle_events: list[str],
) -> None:
    async with app_module.lifespan(FastAPI()):
        pass

    assert lifecycle_events == _CLEANUP_ORDER


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [ValueError("application failed"), asyncio.CancelledError()],
)
async def test_fastapi_cleans_up_and_propagates_body_failure(
    lifecycle_events: list[str],
    error: BaseException,
) -> None:
    with pytest.raises(type(error)) as exc:
        async with app_module.lifespan(FastAPI()):
            raise error

    assert exc.value is error
    assert lifecycle_events == _CLEANUP_ORDER


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_init,expected_cleanup",
    [
        ("init_tracing", ["logger.complete"]),
        ("init_async_db", _CLEANUP_ORDER[2:]),
        ("init_async_redis", _CLEANUP_ORDER[1:]),
        ("init_signature_auth_handler", _CLEANUP_ORDER[1:]),
        ("init_background_task_manager", _CLEANUP_ORDER),
    ],
)
async def test_fastapi_cleans_up_after_partial_startup(
    lifecycle_events: list[str],
    failed_init: str,
    expected_cleanup: list[str],
) -> None:
    error = RuntimeError("startup failed")
    getattr(app_module, failed_init).side_effect = error

    with pytest.raises(RuntimeError, match="startup failed") as exc:
        async with app_module.lifespan(FastAPI()):
            pytest.fail("Failed startup must not yield")

    assert exc.value is error
    assert lifecycle_events == expected_cleanup


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_cleanup", _CLEANUP_ORDER[:-1])
async def test_fastapi_continues_cleanup_after_close_failure(
    lifecycle_events: list[str],
    failed_cleanup: str,
) -> None:
    error = RuntimeError("cleanup failed")
    getattr(app_module, failed_cleanup).side_effect = error

    with pytest.raises(RuntimeError, match="cleanup failed") as exc:
        async with app_module.lifespan(FastAPI()):
            pass

    assert exc.value is error
    assert lifecycle_events == [
        name for name in _CLEANUP_ORDER if name != failed_cleanup
    ]


@pytest.mark.asyncio
async def test_fastapi_waits_for_cleanup_under_anyio_cancellation(
    lifecycle_events: list[str],
) -> None:
    with anyio.CancelScope() as scope:
        async with app_module.lifespan(FastAPI()):
            scope.cancel()
            await anyio.sleep(0)

    assert scope.cancelled_caught
    assert lifecycle_events == _CLEANUP_ORDER


@pytest.mark.asyncio
async def test_fastapi_stops_running_tasks_before_closing_connections(
    lifecycle_events: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(background_tasks, "_background_task_manager", None)
    monkeypatch.setattr(
        app_module,
        "init_background_task_manager",
        background_tasks.init_background_task_manager,
    )
    monkeypatch.setattr(
        app_module,
        "close_background_task_manager",
        background_tasks.close_background_task_manager,
    )
    entered = anyio.Event()

    async def background_job() -> None:
        try:
            entered.set()
            await anyio.sleep_forever()
        finally:
            lifecycle_events.append("background_job.stopped")

    with anyio.CancelScope() as scope:
        async with app_module.lifespan(FastAPI()):
            manager = background_tasks._get_background_task_manager()
            await manager.add_task("lifespan-test", coro_func=background_job)
            await entered.wait()
            scope.cancel()
            await anyio.sleep(0)

    assert scope.cancelled_caught
    assert background_tasks._background_task_manager is None
    assert lifecycle_events == ["background_job.stopped", *_CLEANUP_ORDER[1:]]


@pytest.mark.asyncio
async def test_fastapi_closes_idle_task_group_under_cancellation(
    lifecycle_events: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(background_tasks, "_background_task_manager", None)
    monkeypatch.setattr(
        app_module,
        "init_background_task_manager",
        background_tasks.init_background_task_manager,
    )
    monkeypatch.setattr(
        app_module,
        "close_background_task_manager",
        background_tasks.close_background_task_manager,
    )

    with anyio.CancelScope() as scope:
        async with app_module.lifespan(FastAPI()):
            manager = background_tasks._get_background_task_manager()
            scope.cancel()
            await anyio.sleep(0)

    assert scope.cancelled_caught
    assert background_tasks._background_task_manager is None
    assert manager._tg is None
    assert lifecycle_events == _CLEANUP_ORDER[1:]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_count", [1, 2])
async def test_fastapi_drains_background_tasks_before_propagating_task_cancel(
    lifecycle_events: list[str],
    monkeypatch: pytest.MonkeyPatch,
    cancel_count: int,
) -> None:
    monkeypatch.setattr(background_tasks, "_background_task_manager", None)
    monkeypatch.setattr(
        app_module,
        "init_background_task_manager",
        background_tasks.init_background_task_manager,
    )
    monkeypatch.setattr(
        app_module,
        "close_background_task_manager",
        background_tasks.close_background_task_manager,
    )
    running = anyio.Event()
    draining = anyio.Event()
    release = anyio.Event()

    async def background_job() -> None:
        try:
            running.set()
            await anyio.sleep_forever()
        finally:
            with anyio.CancelScope(shield=True):
                draining.set()
                await release.wait()
                lifecycle_events.append("background_job.stopped")

    async def run_lifespan() -> None:
        async with app_module.lifespan(FastAPI()):
            manager = background_tasks._get_background_task_manager()
            await manager.add_task("draining", coro_func=background_job)
            await running.wait()

    task = asyncio.create_task(run_lifespan())
    await draining.wait()
    try:
        for _ in range(cancel_count):
            task.cancel()
            await asyncio.sleep(0)
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert background_tasks._background_task_manager is None
    assert lifecycle_events == ["background_job.stopped", *_CLEANUP_ORDER[1:]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "close_name",
    ["close_async_redis", "close_async_db", "logger.complete"],
)
@pytest.mark.parametrize("cancel_count", [1, 2])
async def test_fastapi_finishes_resource_close_before_propagating_task_cancel(
    lifecycle_events: list[str],
    monkeypatch: pytest.MonkeyPatch,
    close_name: str,
    cancel_count: int,
) -> None:
    entered_close = asyncio.Event()
    release_close = asyncio.Event()

    async def close_resource() -> None:
        entered_close.set()
        await release_close.wait()
        lifecycle_events.append(close_name)

    if close_name == "logger.complete":
        monkeypatch.setattr(app_module.logger, "complete", close_resource)
    else:
        monkeypatch.setattr(app_module, close_name, close_resource)

    async def run_lifespan() -> None:
        async with app_module.lifespan(FastAPI()):
            pass

    task = asyncio.create_task(run_lifespan())
    await entered_close.wait()
    try:
        for _ in range(cancel_count):
            task.cancel()
            await asyncio.sleep(0)
    finally:
        release_close.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert lifecycle_events == _CLEANUP_ORDER


@pytest.mark.asyncio
async def test_fastapi_preserves_cleanup_failure_after_task_cancellation(
    lifecycle_events: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered_close = asyncio.Event()
    release_close = asyncio.Event()
    error = RuntimeError("Redis close failed")

    async def close_redis() -> None:
        entered_close.set()
        await release_close.wait()
        raise error

    monkeypatch.setattr(app_module, "close_async_redis", close_redis)

    async def run_lifespan() -> None:
        async with app_module.lifespan(FastAPI()):
            pass

    task = asyncio.create_task(run_lifespan())
    await entered_close.wait()
    task.cancel()
    release_close.set()
    with pytest.raises(RuntimeError, match="Redis close failed") as exc:
        await task

    assert exc.value is error
    assert isinstance(error.__context__, asyncio.CancelledError)
    assert lifecycle_events == [
        name for name in _CLEANUP_ORDER if name != "close_async_redis"
    ]


@pytest.mark.asyncio
async def test_fastapi_cleans_up_when_task_cancel_precedes_cleanup_worker(
    lifecycle_events: list[str],
) -> None:
    async def run_lifespan() -> None:
        async with app_module.lifespan(FastAPI()):
            task = asyncio.current_task()
            assert task is not None
            asyncio.get_running_loop().call_soon(task.cancel)

    with pytest.raises(asyncio.CancelledError):
        await asyncio.create_task(run_lifespan())

    assert lifecycle_events == _CLEANUP_ORDER


@pytest.mark.asyncio
async def test_fastapi_preserves_body_and_multiple_cleanup_failures(
    lifecycle_events: list[str],
) -> None:
    body_error = ValueError("application failed")
    redis_error = RuntimeError("Redis close failed")
    db_error = LookupError("database close failed")
    app_module.close_async_redis.side_effect = redis_error
    app_module.close_async_db.side_effect = db_error

    with pytest.raises(LookupError) as exc:
        async with app_module.lifespan(FastAPI()):
            raise body_error

    assert exc.value is db_error
    assert db_error.__context__ is redis_error
    assert redis_error.__context__ is body_error
    assert lifecycle_events == [
        "close_background_task_manager",
        "shutdown_tracing",
        "logger.complete",
    ]


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
