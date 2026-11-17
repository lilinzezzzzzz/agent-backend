"""数据库池独立清理，以及现有生命周期所有者的关闭和取消顺序。"""

import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession

from internal.infra.celery import execution
from internal.infra.database import connection as database
from internal.infra.lifecycle import close_resources
from pkg.database import new_async_engine, new_async_session_maker


@pytest.fixture
def isolated_database(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("_engine", "_read_engine", "_session_maker", "_read_session_maker"):
        monkeypatch.setattr(database, name, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [None, "read", "primary", "both"])
async def test_close_attempts_both_pools_and_preserves_failed_engine(
    isolated_database: None,
    monkeypatch: pytest.MonkeyPatch,
    failed: str | None,
) -> None:
    read_error = RuntimeError("read pool close failed")
    primary_error = ValueError("primary pool close failed")
    read = SimpleNamespace(
        dispose=AsyncMock(
            side_effect=read_error if failed in {"read", "both"} else None,
        ),
    )
    primary = SimpleNamespace(
        dispose=AsyncMock(
            side_effect=primary_error if failed in {"primary", "both"} else None,
        ),
    )
    monkeypatch.setattr(database, "_engine", primary)
    monkeypatch.setattr(database, "_read_engine", read)
    monkeypatch.setattr(database, "_session_maker", object())
    monkeypatch.setattr(database, "_read_session_maker", object())
    if failed is None:
        await database.close_async_db()
    else:
        expected = (
            ExceptionGroup
            if failed == "both"
            else (RuntimeError if failed == "read" else ValueError)
        )
        with pytest.raises(expected) as exc:
            await database.close_async_db()
        if failed == "both":
            assert exc.value.exceptions == (read_error, primary_error)
        else:
            assert exc.value is (read_error if failed == "read" else primary_error)
        with pytest.raises(RuntimeError, match="close_async_db"):
            database.reset_async_db()
        with pytest.raises(RuntimeError, match="close_async_db"):
            database.init_async_db()
    read.dispose.assert_awaited_once()
    primary.dispose.assert_awaited_once()
    assert database._read_engine is (read if failed in {"read", "both"} else None)
    assert database._engine is (primary if failed in {"primary", "both"} else None)
    assert database._session_maker is None
    assert database._read_session_maker is None
    read.dispose.side_effect = None
    primary.dispose.side_effect = None
    await database.close_async_db()
    assert database._engine is None
    assert database._read_engine is None
    assert read.dispose.await_count == (2 if failed in {"read", "both"} else 1)
    assert primary.dispose.await_count == (2 if failed in {"primary", "both"} else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("delayed_pool", ["read", "primary"])
async def test_owner_waits_for_both_pools_before_exit_under_repeated_cancellation(
    isolated_database: None,
    monkeypatch: pytest.MonkeyPatch,
    delayed_pool: str,
) -> None:
    events: list[str] = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def dispose(name: str) -> None:
        if name == delayed_pool:
            entered.set()
            await asyncio.wait_for(release.wait(), timeout=5)
        events.append(name)

    monkeypatch.setattr(
        database,
        "_engine",
        SimpleNamespace(dispose=lambda: dispose("primary")),
    )
    monkeypatch.setattr(
        database,
        "_read_engine",
        SimpleNamespace(dispose=lambda: dispose("read")),
    )

    async def owner() -> None:
        cleanup = AsyncExitStack()
        cleanup.push_async_callback(database.close_async_db)
        try:
            try:
                pass
            finally:
                await close_resources(cleanup)
        finally:
            events.append("owner_exit")

    task = asyncio.create_task(owner())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done()
        assert "owner_exit" not in events
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        assert events == ["read", "primary", "owner_exit"]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only", [False, True])
async def test_owner_waits_for_session_close_then_disposes_engine(
    isolated_database: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    read_only: bool,
) -> None:
    engine = new_async_engine(
        database_uri=URL.create(
            "sqlite+aiosqlite",
            database=str(tmp_path / "lifecycle.db"),
        ),
        echo=False,
    )
    monkeypatch.setattr(database, "_engine", engine)
    monkeypatch.setattr(database, "_session_maker", new_async_session_maker(engine))
    events: list[str] = []
    entered = asyncio.Event()
    release = asyncio.Event()
    original_close = AsyncSession.close
    connection = None

    async def delayed_close(session: AsyncSession) -> None:
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=5)
        await original_close(session)
        events.append("session_closed")

    monkeypatch.setattr(AsyncSession, "close", delayed_close)

    def disposed(_engine) -> None:
        assert connection is not None and connection.closed
        events.append("engine_disposed")

    event.listen(engine.sync_engine, "engine_disposed", disposed)

    async def owner() -> None:
        nonlocal connection
        cleanup = AsyncExitStack()
        cleanup.push_async_callback(database.close_async_db)
        try:
            try:
                provider = (
                    database.get_read_session if read_only else database.get_session
                )
                async with provider(autoflush=False) as session:
                    assert session.autoflush is False
                    connection = await session.connection()
                    assert await session.scalar(text("SELECT 1")) == 1
            finally:
                await close_resources(cleanup)
        finally:
            events.append("owner_exit")

    task = asyncio.create_task(owner())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done()
        assert events == []
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        assert events == ["session_closed", "engine_disposed", "owner_exit"]
        assert database._engine is None
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


def test_celery_disposes_both_pools_before_its_event_loop_exits(
    isolated_database: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    loops: list[asyncio.AbstractEventLoop] = []
    monkeypatch.setattr(
        execution,
        "settings",
        SimpleNamespace(db=SimpleNamespace(echo=False)),
    )
    monkeypatch.setattr(execution, "check_database_revision", AsyncMock())
    for name in ("reset_async_redis", "init_async_redis"):
        monkeypatch.setattr(execution, name, MagicMock())
    monkeypatch.setattr(execution, "close_async_redis", AsyncMock())

    async def dispose(name: str) -> None:
        loop = asyncio.get_running_loop()
        assert not loop.is_closed()
        loops.append(loop)
        await asyncio.sleep(0)
        events.append(name)

    def init(**_kwargs) -> None:
        database._engine = SimpleNamespace(dispose=lambda: dispose("primary"))
        database._read_engine = SimpleNamespace(dispose=lambda: dispose("read"))

    monkeypatch.setattr(execution, "init_async_db", init)

    async def business() -> int:
        events.append("business")
        return 42

    assert execution.run_in_async(business, trace_id="database-lifecycle") == 42
    assert events == ["business", "read", "primary"]
    assert loops and all(loop.is_closed() for loop in loops)
    assert database._engine is None and database._read_engine is None
