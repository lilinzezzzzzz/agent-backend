"""任务资源在数据库版本检查及部分初始化失败后的清理。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest
from billiard.exceptions import SoftTimeLimitExceeded

from internal.infra.celery import execution


@pytest.fixture
def cleanup_events(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []
    monkeypatch.setattr(
        execution,
        "settings",
        SimpleNamespace(db=SimpleNamespace(echo=False)),
    )
    for name in (
        "reset_async_db",
        "reset_async_redis",
        "init_async_db",
        "init_async_redis",
    ):
        monkeypatch.setattr(execution, name, MagicMock())
    monkeypatch.setattr(execution, "check_database_revision", AsyncMock())

    async def close_database() -> None:
        await anyio.sleep(0)
        events.append("database")

    async def close_redis() -> None:
        await anyio.sleep(0)
        events.append("redis")

    monkeypatch.setattr(execution, "close_async_db", close_database)
    monkeypatch.setattr(execution, "close_async_redis", close_redis)
    return events


@pytest.mark.parametrize("failure", [None, "database", "revision", "redis", "business"])
def test_celery_checks_revision_before_business_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    events: list[str] = []
    error = RuntimeError("task resource failed")

    def step(name: str) -> None:
        events.append(name)
        if failure == name:
            raise error

    monkeypatch.setattr(
        execution,
        "settings",
        SimpleNamespace(db=SimpleNamespace(echo=False)),
    )
    monkeypatch.setattr(execution, "reset_async_db", MagicMock())
    monkeypatch.setattr(execution, "reset_async_redis", MagicMock())
    monkeypatch.setattr(execution, "init_async_db", lambda **kwargs: step("database"))
    monkeypatch.setattr(execution, "init_async_redis", lambda: step("redis"))

    async def check_revision() -> None:
        step("revision")

    monkeypatch.setattr(execution, "check_database_revision", check_revision)

    async def close_database() -> None:
        events.append("close_database")

    async def close_redis() -> None:
        events.append("close_redis")

    monkeypatch.setattr(execution, "close_async_db", close_database)
    monkeypatch.setattr(execution, "close_async_redis", close_redis)

    async def business() -> int:
        step("business")
        return 42

    if failure:
        with pytest.raises(RuntimeError) as exc:
            execution.run_in_async(business, trace_id="test-trace")
        assert exc.value is error
    else:
        assert execution.run_in_async(business, trace_id="test-trace") == 42
    stages = ["database", "revision", "redis", "business"]
    expected = stages if failure is None else stages[: stages.index(failure) + 1]
    expected += (
        ["close_database"]
        if failure in {"database", "revision"}
        else ["close_redis", "close_database"]
    )
    assert events == expected


@pytest.mark.parametrize(
    "error",
    [asyncio.CancelledError(), BaseException("task aborted"), SoftTimeLimitExceeded()],
)
def test_celery_cleans_up_and_preserves_base_exception(
    cleanup_events: list[str],
    error: BaseException,
) -> None:
    async def business() -> None:
        raise error

    with pytest.raises(type(error)) as exc:
        execution.run_in_async(business, trace_id="test-trace")
    assert exc.value is error
    assert cleanup_events == ["redis", "database"]


def test_celery_finishes_cleanup_under_anyio_cancellation(
    cleanup_events: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_run = anyio.run
    scopes: list[anyio.CancelScope] = []

    def run_in_scope(callback):
        async def scoped() -> None:
            with anyio.CancelScope() as scope:
                scopes.append(scope)
                await callback()

        return real_run(scoped)

    monkeypatch.setattr(execution.anyio, "run", run_in_scope)

    async def business() -> None:
        scopes[0].cancel()
        await anyio.sleep(0)

    execution.run_in_async(business, trace_id="test-trace")
    assert scopes[0].cancelled_caught
    assert cleanup_events == ["redis", "database"]


@pytest.mark.parametrize("close_name", ["close_async_db", "close_async_redis"])
@pytest.mark.parametrize("cancel_count", [1, 2])
def test_celery_finishes_cleanup_before_propagating_task_cancellation(
    cleanup_events: list[str],
    monkeypatch: pytest.MonkeyPatch,
    close_name: str,
    cancel_count: int,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def close_resource() -> None:
        entered.set()
        await release.wait()
        cleanup_events.append("database" if close_name == "close_async_db" else "redis")

    monkeypatch.setattr(execution, close_name, close_resource)

    async def business() -> None:
        caller = asyncio.current_task()
        assert caller is not None

        async def cancel_during_cleanup() -> None:
            await entered.wait()
            for _ in range(cancel_count):
                caller.cancel()
                await asyncio.sleep(0)
            release.set()

        asyncio.create_task(cancel_during_cleanup())

    with pytest.raises(asyncio.CancelledError):
        execution.run_in_async(business, trace_id="test-trace")
    assert cleanup_events == ["redis", "database"]


@pytest.mark.parametrize("close_name", ["close_async_db", "close_async_redis"])
def test_celery_continues_cleanup_and_preserves_exception_chain(
    cleanup_events: list[str],
    monkeypatch: pytest.MonkeyPatch,
    close_name: str,
) -> None:
    body_error = ValueError("business failed")
    close_error = RuntimeError("resource close failed")

    async def close_resource() -> None:
        await anyio.sleep(0)
        cleanup_events.append("database" if close_name == "close_async_db" else "redis")
        raise close_error

    monkeypatch.setattr(execution, close_name, close_resource)

    async def business() -> None:
        raise body_error

    with pytest.raises(RuntimeError) as exc:
        execution.run_in_async(business, trace_id="test-trace")
    assert exc.value is close_error
    assert close_error.__context__ is body_error
    assert cleanup_events == ["redis", "database"]


def test_celery_preserves_cleanup_failure_during_task_cancellation(
    cleanup_events: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    close_error = RuntimeError("Redis close failed")

    async def close_redis() -> None:
        entered.set()
        await release.wait()
        cleanup_events.append("redis")
        raise close_error

    monkeypatch.setattr(execution, "close_async_redis", close_redis)

    async def business() -> None:
        caller = asyncio.current_task()
        assert caller is not None

        async def cancel_during_cleanup() -> None:
            await entered.wait()
            caller.cancel()
            await asyncio.sleep(0)
            release.set()

        asyncio.create_task(cancel_during_cleanup())

    with pytest.raises(RuntimeError) as exc:
        execution.run_in_async(business, trace_id="test-trace")
    assert exc.value is close_error
    assert isinstance(close_error.__context__, asyncio.CancelledError)
    assert cleanup_events == ["redis", "database"]
