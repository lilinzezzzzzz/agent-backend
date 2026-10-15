import asyncio
from collections.abc import Generator
from threading import Event

import anyio
import pytest

import pkg.concurrency.manager as manager_module
from pkg.concurrency import AnyioTaskHandler


class ControlledJob:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.finished = Event()

    def __call__(self) -> str:
        self.started.set()
        try:
            # Bound failures in the old implementation without relying on exact timing.
            self.release.wait(timeout=2)
            return "finished"
        finally:
            self.finished.set()


@pytest.fixture
def job() -> Generator[ControlledJob, None, None]:
    worker = ControlledJob()
    try:
        yield worker
    finally:
        worker.release.set()


async def wait_until_started(job: ControlledJob) -> None:
    with anyio.fail_after(2):
        while not job.started.is_set():
            await anyio.sleep(0.001)


async def release_and_wait(manager: AnyioTaskHandler, job: ControlledJob) -> None:
    job.release.set()
    with anyio.fail_after(2):
        while not job.finished.is_set() or manager._thread_limiter.borrowed_tokens:
            await anyio.sleep(0.001)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancellable", [False, True])
async def test_thread_timeout_returns_before_worker_finishes(
    cancellable: bool, job: ControlledJob
) -> None:
    manager = AnyioTaskHandler()
    try:
        with pytest.raises(TimeoutError):
            await manager.run_in_thread(job, timeout=0.1, cancellable=cancellable)
        assert job.started.is_set()
        assert not job.finished.is_set()
    finally:
        await release_and_wait(manager, job)


@pytest.mark.asyncio
async def test_batch_timeout_preserves_order_and_successful_results(
    job: ControlledJob,
) -> None:
    manager = AnyioTaskHandler()

    def work(slow: bool) -> str:
        return job() if slow else "quick"

    try:
        results = await manager.run_in_threads(
            work, args_tuple_list=[(True,), (False,)], timeout=0.1
        )
        assert results == [None, "quick"]
        assert not job.finished.is_set()
    finally:
        await release_and_wait(manager, job)


@pytest.mark.asyncio
async def test_timed_out_worker_keeps_capacity_until_it_finishes(
    job: ControlledJob,
) -> None:
    manager = AnyioTaskHandler()
    manager._thread_limiter = anyio.CapacityLimiter(1)
    second_started = Event()

    def second() -> str:
        second_started.set()
        return "second"

    try:
        with pytest.raises(TimeoutError):
            await manager.run_in_thread(job, timeout=0.1)
        assert manager._thread_limiter.borrowed_tokens == 1
        with pytest.raises(TimeoutError):
            await manager.run_in_thread(second, timeout=0.1)
        assert not second_started.is_set()
    finally:
        await release_and_wait(manager, job)
    assert await manager.run_in_thread(second, timeout=1) == "second"
    assert manager._thread_limiter.borrowed_tokens == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("cancellable", [False, True])
async def test_external_anyio_cancellation_obeys_cancellable(
    cancellable: bool, job: ControlledJob
) -> None:
    manager = AnyioTaskHandler()
    done = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    results: list[str] = []

    async def call() -> None:
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            results.append(await manager.run_in_thread(job, cancellable=cancellable))
        done.set()

    try:
        async with anyio.create_task_group() as group:
            group.start_soon(call)
            await wait_until_started(job)
            scopes[0].cancel()
            if cancellable:
                with anyio.fail_after(1):
                    await done.wait()
                assert results == []
                assert scopes[0].cancelled_caught
                assert not job.finished.is_set()
                assert manager._thread_limiter.borrowed_tokens == 1
            else:
                await anyio.sleep(0.02)
                assert not done.is_set()
                job.release.set()
                with anyio.fail_after(1):
                    await done.wait()
                assert results == ["finished"]
    finally:
        await release_and_wait(manager, job)


@pytest.mark.asyncio
async def test_own_timeout_still_fires_when_external_cancellation_is_shielded(
    job: ControlledJob,
) -> None:
    manager = AnyioTaskHandler()
    done = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def call() -> None:
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            with pytest.raises(TimeoutError):
                await manager.run_in_thread(job, timeout=0.1, cancellable=False)
        done.set()

    try:
        async with anyio.create_task_group() as group:
            group.start_soon(call)
            await wait_until_started(job)
            scopes[0].cancel()
            with anyio.fail_after(1):
                await done.wait()
            assert not job.finished.is_set()
    finally:
        await release_and_wait(manager, job)


@pytest.mark.asyncio
async def test_batch_external_cancellation_does_not_return_partial_success(
    job: ControlledJob,
) -> None:
    manager = AnyioTaskHandler()
    done = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    returned = False

    async def call() -> None:
        nonlocal returned
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            await manager.run_in_threads(job, args_tuple_list=[()], cancellable=True)
            returned = True
        done.set()

    try:
        async with anyio.create_task_group() as group:
            group.start_soon(call)
            await wait_until_started(job)
            scopes[0].cancel()
            with anyio.fail_after(1):
                await done.wait()
            assert not returned
            assert not job.finished.is_set()
    finally:
        await release_and_wait(manager, job)


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [None, 0, -1])
async def test_disabled_timeout_and_keyword_arguments(timeout: float | None) -> None:
    manager = AnyioTaskHandler()

    def work(*, value: str) -> str:
        return value

    assert (
        await manager.run_in_thread(work, kwargs_dict={"value": "ok"}, timeout=timeout)
        == "ok"
    )
    assert manager._thread_limiter.borrowed_tokens == 0


@pytest.mark.asyncio
async def test_thread_exception_propagates_and_releases_capacity() -> None:
    manager = AnyioTaskHandler()

    def fail() -> None:
        raise ValueError("worker failure")

    with pytest.raises(ValueError, match="worker failure"):
        await manager.run_in_thread(fail, timeout=1)
    assert manager._thread_limiter.borrowed_tokens == 0


@pytest.mark.asyncio
async def test_direct_asyncio_cancellation_keeps_running_worker_capacity(
    job: ControlledJob,
) -> None:
    manager = AnyioTaskHandler()
    task = asyncio.create_task(manager.run_in_thread(job, cancellable=False))
    try:
        await wait_until_started(job)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not job.finished.is_set()
        assert manager._thread_limiter.borrowed_tokens == 1
    finally:
        await release_and_wait(manager, job)


@pytest.mark.asyncio
async def test_failed_dispatch_releases_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = AnyioTaskHandler()

    async def fail_dispatch(*args, **kwargs):
        raise RuntimeError("worker could not start")

    monkeypatch.setattr(manager_module, "anyio_run_in_thread", fail_dispatch)
    with pytest.raises(RuntimeError, match="worker could not start"):
        await manager.run_in_thread(lambda: None, timeout=1)
    assert manager._thread_limiter.borrowed_tokens == 0


@pytest.mark.asyncio
async def test_cancelled_dispatch_cannot_start_work_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = AnyioTaskHandler()
    pending = []
    ran = Event()

    async def delay_dispatch(func, *args, **kwargs):
        pending.append(func)
        await anyio.sleep_forever()

    monkeypatch.setattr(manager_module, "anyio_run_in_thread", delay_dispatch)
    with pytest.raises(TimeoutError):
        await manager.run_in_thread(ran.set, timeout=0.1)
    assert manager._thread_limiter.borrowed_tokens == 0
    await anyio.to_thread.run_sync(pending[0])
    assert not ran.is_set()


@pytest.mark.asyncio
async def test_shutdown_after_timeout_does_not_claim_worker_has_stopped(
    job: ControlledJob,
) -> None:
    manager = AnyioTaskHandler()
    await manager.start()
    try:
        with pytest.raises(TimeoutError):
            await manager.run_in_thread(job, timeout=0.1)
        await manager.shutdown()
        assert not job.finished.is_set()
        assert manager._thread_limiter.borrowed_tokens == 1
    finally:
        await release_and_wait(manager, job)
        if manager._tg_started:
            await manager.shutdown()
