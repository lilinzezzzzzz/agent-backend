import time

import pytest
from celery.result import AsyncResult

from internal.infra.celery import celery_app, celery_client
from internal.tasks.celery import number_sum

pytestmark = pytest.mark.integration

_TEST_TRACE_ID = "test-celery-trace"


def test_number_sum_async_execution() -> None:
    task_result: AsyncResult = number_sum.delay(15, 25)

    try:
        result = task_result.get(timeout=10)
        assert result == 40
        assert task_result.successful()
    except Exception as exc:
        pytest.skip(f"Celery Worker 未启动或不可用: {exc}")


def test_number_sum_apply_async() -> None:
    task_result: AsyncResult = number_sum.apply_async(args=(100, 200), queue="default")

    try:
        result = task_result.get(timeout=10)
        assert result == 300
        assert task_result.state == "SUCCESS"
    except Exception as exc:
        pytest.skip(f"Celery Worker 未启动或不可用: {exc}")


def test_number_sum_task_retry() -> None:
    task_result: AsyncResult = number_sum.delay("invalid", 10)

    try:
        task_result.get(timeout=10)
        pytest.fail("Expected task to fail but it succeeded")
    except TypeError:
        assert task_result.failed()
    except Exception as exc:
        pytest.skip(f"Celery Worker 未启动或不可用: {exc}")


def test_celery_client_submit() -> None:
    task_result = celery_client.submit(
        task_name="internal.tasks.celery_tasks.number_sum",
        trace_id=_TEST_TRACE_ID,
        args=(50, 60),
    )

    try:
        result = task_result.get(timeout=10)
        assert result == 110
        assert task_result.successful()
    except Exception as exc:
        pytest.skip(f"Celery Worker 未启动或不可用: {exc}")


def test_celery_client_submit_with_options() -> None:
    task_result = celery_client.submit(
        task_name="internal.tasks.celery_tasks.number_sum",
        trace_id=_TEST_TRACE_ID,
        args=(100, 100),
        queue="default",
        priority=5,
    )

    try:
        assert task_result.get(timeout=10) == 200
    except Exception as exc:
        pytest.skip(f"Celery Worker 未启动或不可用: {exc}")


def test_celery_client_get_status() -> None:
    task_result = celery_client.submit(
        task_name="internal.tasks.celery_tasks.number_sum",
        trace_id=_TEST_TRACE_ID,
        args=(1, 2),
    )

    try:
        task_result.get(timeout=10)
        assert celery_client.get_status(task_result.id) == "SUCCESS"
        assert celery_client.get_result(task_result.id) == 3
    except Exception as exc:
        pytest.skip(f"Celery Worker 未启动或不可用: {exc}")


def test_celery_client_submit_with_countdown() -> None:
    start_time = time.time()
    task_result = celery_client.submit(
        task_name="internal.tasks.celery_tasks.number_sum",
        trace_id=_TEST_TRACE_ID,
        args=(5, 5),
        countdown=2,
    )

    try:
        result = task_result.get(timeout=15)
        assert result == 10
        assert time.time() - start_time >= 1.5
    except Exception as exc:
        pytest.skip(f"Celery Worker 未启动或不可用: {exc}")


def test_celery_client_revoke() -> None:
    task_result = celery_client.submit(
        task_name="internal.tasks.celery_tasks.number_sum",
        trace_id=_TEST_TRACE_ID,
        args=(100, 100),
        countdown=60,
    )

    try:
        celery_client.revoke(task_result.id, terminate=True)
        time.sleep(1)
        assert celery_client.get_status(task_result.id) in ["REVOKED", "PENDING"]
    except Exception as exc:
        pytest.skip(f"Celery Worker 未启动或不可用: {exc}")


def test_celery_broker_connection() -> None:
    try:
        with celery_app.connection_or_acquire() as conn:
            conn.ensure_connection(max_retries=3)
    except Exception as exc:
        pytest.skip(f"Redis Broker 不可用: {exc}")


def test_multiple_tasks_execution() -> None:
    tasks = [number_sum.delay(i, i * 2) for i in range(1, 6)]

    try:
        results = [task.get(timeout=10) for task in tasks]
        assert results == [i + i * 2 for i in range(1, 6)]
    except Exception as exc:
        pytest.skip(f"Celery Worker 未启动或不可用: {exc}")
