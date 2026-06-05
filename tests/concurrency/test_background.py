import asyncio
from typing import Any

import pytest

from pkg.concurrency import asyncio_run_background


@pytest.mark.asyncio
async def test_asyncio_run_background_executes_coroutine() -> None:
    results: list[str] = []

    async def worker(*, value: str) -> str:
        await asyncio.sleep(0)
        results.append(value)
        return value

    task = asyncio_run_background(worker(value="done"))

    assert task.get_name() == "worker"
    assert await task == "done"
    assert results == ["done"]


@pytest.mark.asyncio
async def test_asyncio_run_background_uses_bound_method_name() -> None:
    class Worker:
        async def run(self) -> str:
            await asyncio.sleep(0)
            return "done"

    task = asyncio_run_background(Worker().run())

    assert task.get_name() == "Worker.run"
    assert await task == "done"


@pytest.mark.asyncio
async def test_asyncio_run_background_calls_on_error() -> None:
    handled_errors: list[Exception] = []

    async def worker() -> None:
        raise ValueError("boom")

    def on_error(exc: Exception) -> None:
        handled_errors.append(exc)

    asyncio_run_background(worker(), on_error=on_error)
    for _ in range(10):
        if handled_errors:
            break
        await asyncio.sleep(0)

    assert len(handled_errors) == 1
    assert isinstance(handled_errors[0], ValueError)
    assert str(handled_errors[0]) == "boom"


@pytest.mark.asyncio
async def test_asyncio_run_background_uses_loop_exception_handler() -> None:
    handled_contexts: list[dict[str, Any]] = []

    async def worker() -> None:
        raise ValueError("boom")

    loop = asyncio.get_running_loop()
    original_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: handled_contexts.append(context))
    asyncio_run_background(worker())
    for _ in range(10):
        if handled_contexts:
            break
        await asyncio.sleep(0)
    loop.set_exception_handler(original_handler)

    assert handled_contexts
    assert handled_contexts[0]["message"] == "background task worker failed"
    assert isinstance(handled_contexts[0]["exception"], ValueError)
    assert handled_contexts[0]["task"].get_name() == "worker"


@pytest.mark.asyncio
async def test_asyncio_run_background_reports_on_error_failure() -> None:
    handled_contexts: list[dict[str, Any]] = []

    async def worker() -> None:
        raise ValueError("boom")

    def on_error(_exc: Exception) -> None:
        raise RuntimeError("handler failed")

    loop = asyncio.get_running_loop()
    original_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: handled_contexts.append(context))
    asyncio_run_background(worker(), on_error=on_error)
    for _ in range(10):
        if handled_contexts:
            break
        await asyncio.sleep(0)
    loop.set_exception_handler(original_handler)

    assert handled_contexts
    assert (
        handled_contexts[0]["message"] == "background task worker error handler failed"
    )
    assert isinstance(handled_contexts[0]["exception"], RuntimeError)
    assert isinstance(handled_contexts[0]["source_exception"], ValueError)
