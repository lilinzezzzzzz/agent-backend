from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from pkg.celery_queue import CeleryClient


@pytest.fixture
def celery_client() -> CeleryClient:
    return CeleryClient(
        app_name="test_celery_client",
        broker_url="memory://",
        backend_url="cache+memory://",
    )


def test_submit_merges_trace_header_without_custom_task_id(
    celery_client: CeleryClient,
) -> None:
    send_task = MagicMock(return_value=object())
    celery_client.app.send_task = send_task

    result = celery_client.submit(
        task_name="internal.tasks.celery_tasks.send_welcome_email",
        trace_id="caller-trace-id",
        args=(10,),
        headers={"tenant_id": "tenant-1", "trace_id": "stale-trace-id"},
    )

    assert result is send_task.return_value
    send_task.assert_called_once_with(
        name="internal.tasks.celery_tasks.send_welcome_email",
        args=(10,),
        kwargs={},
        queue="default",
        headers={"tenant_id": "tenant-1", "trace_id": "caller-trace-id"},
    )


def test_submit_accepts_stable_task_id(celery_client: CeleryClient) -> None:
    send_task = MagicMock(return_value=object())
    celery_client.app.send_task = send_task

    celery_client.submit(
        task_name="internal.tasks.celery_tasks.number_sum",
        trace_id="trace-1",
        task_id="task_123",
    )

    send_task.assert_called_once_with(
        name="internal.tasks.celery_tasks.number_sum",
        args=(),
        kwargs={},
        queue="default",
        headers={"trace_id": "trace-1"},
        task_id="task_123",
    )


@pytest.mark.asyncio
async def test_async_submit_offloads_send_task(celery_client: CeleryClient) -> None:
    send_task = MagicMock(return_value=object())
    celery_client.app.send_task = send_task

    result = await celery_client.async_submit(
        task_name="internal.tasks.celery_tasks.number_sum",
        trace_id="trace-1",
        args=[1, 2],
        queue="priority",
        task_id="task_123",
        retry=False,
    )

    assert result is send_task.return_value
    send_task.assert_called_once_with(
        name="internal.tasks.celery_tasks.number_sum",
        args=(1, 2),
        kwargs={},
        queue="priority",
        headers={"trace_id": "trace-1"},
        task_id="task_123",
        retry=False,
    )


@pytest.mark.asyncio
async def test_async_get_result_offloads_sync_method(
    celery_client: CeleryClient,
) -> None:
    get_result = MagicMock(return_value={"ok": True})
    celery_client.get_result = get_result

    result = await celery_client.async_get_result(
        "task_123", timeout=1.5, propagate=False
    )

    assert result == {"ok": True}
    get_result.assert_called_once_with("task_123", 1.5, propagate=False)


@pytest.mark.asyncio
async def test_async_get_status_offloads_sync_method(
    celery_client: CeleryClient,
) -> None:
    get_status = MagicMock(return_value="SUCCESS")
    celery_client.get_status = get_status

    result = await celery_client.async_get_status("task_123")

    assert result == "SUCCESS"
    get_status.assert_called_once_with("task_123")


@pytest.mark.asyncio
async def test_async_revoke_offloads_sync_method(celery_client: CeleryClient) -> None:
    revoke = MagicMock(return_value=None)
    celery_client.revoke = revoke

    await celery_client.async_revoke("task_123", terminate=True)

    revoke.assert_called_once_with("task_123", terminate=True)


def test_submit_requires_trace_id(celery_client: CeleryClient) -> None:
    submit = cast(Any, celery_client.submit)
    with pytest.raises(TypeError, match="trace_id"):
        submit(task_name="internal.tasks.celery_tasks.number_sum")


def test_submit_rejects_non_string_trace_id(celery_client: CeleryClient) -> None:
    submit = cast(Any, celery_client.submit)
    with pytest.raises(TypeError, match="trace_id must be a string"):
        submit(task_name="internal.tasks.celery_tasks.number_sum", trace_id=123)


def test_submit_rejects_empty_trace_id(celery_client: CeleryClient) -> None:
    with pytest.raises(ValueError, match="trace_id is mandatory"):
        celery_client.submit(
            task_name="internal.tasks.celery_tasks.number_sum", trace_id=""
        )


def test_submit_rejects_non_string_task_id(celery_client: CeleryClient) -> None:
    submit = cast(Any, celery_client.submit)
    with pytest.raises(TypeError, match="task_id must be a string"):
        submit(
            task_name="internal.tasks.celery_tasks.number_sum",
            trace_id="trace-1",
            task_id=123,
        )


def test_submit_rejects_empty_task_id(celery_client: CeleryClient) -> None:
    with pytest.raises(ValueError, match="task_id cannot be empty"):
        celery_client.submit(
            task_name="internal.tasks.celery_tasks.number_sum",
            trace_id="trace-1",
            task_id="",
        )


@pytest.mark.parametrize("trace_id", ["   ", "unknown", "-"])
def test_submit_rejects_invalid_trace_id(
    celery_client: CeleryClient, trace_id: str
) -> None:
    with pytest.raises(ValueError, match="trace_id"):
        celery_client.submit(
            task_name="internal.tasks.celery_tasks.number_sum",
            trace_id=trace_id,
        )
