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


def test_submit_rejects_caller_task_id(celery_client: CeleryClient) -> None:
    with pytest.raises(TypeError, match="task_id is generated automatically"):
        celery_client.submit(
            task_name="internal.tasks.celery_tasks.number_sum",
            trace_id="trace-1",
            task_id="caller-task-id",
        )


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
        celery_client.submit(task_name="internal.tasks.celery_tasks.number_sum", trace_id="")
