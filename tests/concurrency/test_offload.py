import asyncio
from typing import Any

import anyio
import pytest

from pkg.concurrency import anyio_gather


@pytest.mark.asyncio
async def test_anyio_gather_preserves_input_order_and_none_results() -> None:
    async def job(delay: float, result: Any) -> Any:
        await anyio.sleep(delay)
        return result

    results = await anyio_gather(
        job(0.02, "first"),
        job(0, None),
        job(0.01, "third"),
    )

    assert results == ["first", None, "third"]


@pytest.mark.asyncio
async def test_anyio_gather_cancels_siblings_after_error() -> None:
    sibling_cancelled = anyio.Event()

    async def fail() -> None:
        await anyio.sleep(0)
        raise ValueError("invalid")

    async def wait_forever() -> None:
        try:
            await anyio.sleep_forever()
        finally:
            sibling_cancelled.set()

    with pytest.raises(ValueError, match="invalid"):
        await anyio_gather(wait_forever(), fail())

    assert sibling_cancelled.is_set()


@pytest.mark.asyncio
async def test_anyio_gather_propagates_external_cancellation() -> None:
    completed = False

    async def wait_forever() -> None:
        nonlocal completed
        try:
            await anyio.sleep_forever()
        finally:
            completed = True

    with anyio.move_on_after(0.01) as cancel_scope:
        await anyio_gather(wait_forever())

    assert cancel_scope.cancel_called
    assert completed


@pytest.mark.asyncio
async def test_anyio_gather_propagates_child_cancellation() -> None:
    async def cancel() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await anyio_gather(cancel())
