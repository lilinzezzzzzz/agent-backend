import asyncio
import os
import threading
import time
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import anyio
import pytest

from pkg.concurrency import AnyioTaskHandler


async def async_job(duration: float, result: Any) -> Any:
    await anyio.sleep(duration)
    return result


async def async_job_error() -> None:
    await anyio.sleep(0.1)
    raise ValueError("Test Error")


def sync_job(duration: float, result: str) -> str:
    time.sleep(duration)
    return result


def sync_cpu_job(x: int, y: int) -> int:
    return x + y


def get_thread_id() -> int:
    return threading.get_ident()


def get_process_id() -> int:
    return os.getpid()


@asynccontextmanager
async def _manager_scope() -> AsyncGenerator[AnyioTaskHandler, None]:
    mgr = AnyioTaskHandler()
    mgr.max_queue = 100
    await mgr.start()
    try:
        yield mgr
    finally:
        await mgr.shutdown()


@pytest.fixture
def manager_scope() -> AbstractAsyncContextManager[AnyioTaskHandler]:
    return _manager_scope()


@pytest.mark.asyncio
async def test_shutdown_rejects_submission_waiting_for_lock(
    manager_scope: AbstractAsyncContextManager[AnyioTaskHandler],
) -> None:
    async with manager_scope as manager:
        started = asyncio.Event()

        async def job() -> None:
            pass

        async def submit() -> bool:
            started.set()
            return await manager.add_task("queued", coro_func=job)

        await manager._lock.acquire()
        pending = asyncio.create_task(submit())
        try:
            await started.wait()
            await manager.shutdown()
        finally:
            manager._lock.release()

        with pytest.raises(RuntimeError, match="shutting down"):
            await pending
        assert not manager.tasks


@pytest.mark.asyncio
class TestAnyioTaskManager:
    async def test_add_task_success(
        self,
        manager_scope: AbstractAsyncContextManager[AnyioTaskHandler],
    ) -> None:
        async with manager_scope as manager:
            result_box: dict[str, Any] = {"value": None}

            async def side_effect_job() -> None:
                await anyio.sleep(0.1)
                result_box["value"] = "done"

            await manager.add_task("task_1", coro_func=side_effect_job)

            status = await manager.get_task_status()
            assert "task_1" in status
            assert status["task_1"] is True

            for _ in range(20):
                status = await manager.get_task_status()
                if "task_1" not in status:
                    break
                await anyio.sleep(0.05)

            assert result_box["value"] == "done"
            assert "task_1" not in status

    async def test_add_task_duplicate(
        self,
        manager_scope: AbstractAsyncContextManager[AnyioTaskHandler],
    ) -> None:
        async with manager_scope as manager:

            async def long_job() -> None:
                await anyio.sleep(1)

            success1 = await manager.add_task("dup_task", coro_func=long_job)
            success2 = await manager.add_task("dup_task", coro_func=long_job)

            assert success1 is True
            assert success2 is False

    async def test_add_task_timeout(
        self,
        manager_scope: AbstractAsyncContextManager[AnyioTaskHandler],
    ) -> None:
        async with manager_scope as manager:
            await manager.add_task(
                "timeout_task",
                coro_func=async_job,
                args_tuple=(1.0, "res"),
                timeout=0.1,
            )

            info = manager.tasks["timeout_task"]
            for _ in range(20):
                if info.status != "running":
                    break
                await anyio.sleep(0.05)

            assert info.status == "timeout"
            assert isinstance(info.exception, TimeoutError)

    async def test_cancel_task(
        self,
        manager_scope: AbstractAsyncContextManager[AnyioTaskHandler],
    ) -> None:
        async with manager_scope as manager:

            async def forever_job() -> None:
                await anyio.sleep(10)

            await manager.add_task("cancel_me", coro_func=forever_job)
            await anyio.sleep(0.01)

            cancel_success = await manager.cancel_task("cancel_me")
            assert cancel_success is True

            task_removed = False
            for _ in range(20):
                status = await manager.get_task_status()
                if "cancel_me" not in status:
                    task_removed = True
                    break
                await anyio.sleep(0.05)

            assert task_removed is True, (
                "Task 'cancel_me' should be removed from tasks list"
            )

    async def test_gather_concurrency_success(
        self,
        manager_scope: AbstractAsyncContextManager[AnyioTaskHandler],
    ) -> None:
        async with manager_scope as manager:
            args = [(0.1, "1"), (0.1, "2"), (0.1, "3")]
            results = await manager.run_gather_with_concurrency(
                async_job, args, jitter=0
            )
            assert results == ["1", "2", "3"]

    async def test_gather_concurrency_partial_timeout(
        self,
        manager_scope: AbstractAsyncContextManager[AnyioTaskHandler],
    ) -> None:
        async with manager_scope as manager:
            args = [(0.1, "fast"), (1.0, "slow")]
            results = await manager.run_gather_with_concurrency(
                async_job, args, task_timeout=0.2, jitter=0
            )
            assert results[0] == "fast"
            assert results[1] is None

    async def test_gather_concurrency_global_timeout(
        self,
        manager_scope: AbstractAsyncContextManager[AnyioTaskHandler],
    ) -> None:
        async with manager_scope as manager:
            args = [(0.5, "A"), (0.5, "B")]
            results = await manager.run_gather_with_concurrency(
                async_job, args, global_timeout=0.2, jitter=0
            )
            assert results == [None, None]

    async def test_run_in_thread(
        self,
        manager_scope: AbstractAsyncContextManager[AnyioTaskHandler],
    ) -> None:
        async with manager_scope as manager:
            start_time = time.time()
            res = await manager.run_in_thread(sync_job, args_tuple=(0.2, "thread_res"))
            end_time = time.time()

            assert res == "thread_res"
            assert end_time - start_time >= 0.2

            main_thread = threading.get_ident()
            worker_thread = await manager.run_in_thread(get_thread_id)
            assert main_thread != worker_thread

    async def test_run_in_process(
        self,
        manager_scope: AbstractAsyncContextManager[AnyioTaskHandler],
    ) -> None:
        async with manager_scope as manager:
            res = await manager.run_in_process(sync_cpu_job, args_tuple=(5, 10))
            assert res == 15

            main_pid = os.getpid()
            worker_pid = await manager.run_in_process(get_process_id)
            assert main_pid != worker_pid

    async def test_run_in_threads_batch(
        self,
        manager_scope: AbstractAsyncContextManager[AnyioTaskHandler],
    ) -> None:
        async with manager_scope as manager:
            args_list = [(0.1, "t1"), (0.1, "t2")]
            results = await manager.run_in_threads(sync_job, args_tuple_list=args_list)
            assert results == ["t1", "t2"]

    async def test_run_in_processes_batch(
        self,
        manager_scope: AbstractAsyncContextManager[AnyioTaskHandler],
    ) -> None:
        async with manager_scope as manager:
            args_list = [(1, 1), (2, 2)]
            results = await manager.run_in_processes(
                sync_cpu_job, args_tuple_list=args_list
            )
            assert results == [2, 4]

    async def test_task_internal_exception(
        self,
        manager_scope: AbstractAsyncContextManager[AnyioTaskHandler],
    ) -> None:
        async with manager_scope as manager:
            await manager.add_task("error_task", coro_func=async_job_error)

            info = manager.tasks["error_task"]
            for _ in range(20):
                if info.status != "running":
                    break
                await anyio.sleep(0.05)

            assert info.status == "failed"
            assert isinstance(info.exception, ValueError)

    async def test_shutdown_with_running_tasks(
        self,
        manager_scope: AbstractAsyncContextManager[AnyioTaskHandler],
    ) -> None:
        async with manager_scope as manager:

            async def slow_task() -> None:
                try:
                    await anyio.sleep(2)
                except anyio.get_cancelled_exc_class():
                    pass

            await manager.add_task("closing_task", coro_func=slow_task)

            start_shutdown = time.time()
            await manager.shutdown()
            end_shutdown = time.time()

            assert end_shutdown - start_shutdown < 2.0
