import anyio

from internal.tasks import reconciler, scheduler
from internal.tasks.constants import (
    EXECUTION_RECONCILER_TASK_NAME,
    QUEUED_RECONCILER_TASK_NAME,
    SUBMITTING_RECONCILER_TASK_NAME,
)


class FakeSettings:
    CELERY_RECONCILER_BATCH_SIZE = 25
    CELERY_PUBLISH_CONFIRM_TIMEOUT_SECONDS = 30


class FakeCeleryTaskDao:
    def __init__(self) -> None:
        self.fail_stale_submitting_calls: list[dict[str, int]] = []
        self.fail_expired_queued_calls: list[dict[str, int]] = []
        self.reconcile_expired_execution_calls: list[dict[str, int]] = []

    async def fail_stale_submitting(self, **kwargs: int) -> list[int]:
        self.fail_stale_submitting_calls.append(kwargs)
        return [1, 2]

    async def fail_expired_queued(self, **kwargs: int) -> list[int]:
        self.fail_expired_queued_calls.append(kwargs)
        return [3]

    async def reconcile_expired_execution(self, **kwargs: int) -> list[int]:
        self.reconcile_expired_execution_calls.append(kwargs)
        return [4, 5, 6]


def _run_async(coro_func, *, trace_id: str):
    assert isinstance(trace_id, str)
    assert trace_id.startswith("celery-")
    return anyio.run(coro_func)


def test_submitting_reconciler_only_marks_stale_submitting(
    monkeypatch,
) -> None:
    dao = FakeCeleryTaskDao()
    monkeypatch.setattr(reconciler, "settings", FakeSettings)
    monkeypatch.setattr(reconciler, "new_celery_task_dao", lambda: dao)
    monkeypatch.setattr(reconciler, "run_in_async", _run_async)

    result = reconciler.fail_stale_submitting.run()

    assert result == {"submitting_failed_count": 2}
    assert dao.fail_stale_submitting_calls == [
        {
            "batch_size": FakeSettings.CELERY_RECONCILER_BATCH_SIZE,
            "stale_seconds": FakeSettings.CELERY_PUBLISH_CONFIRM_TIMEOUT_SECONDS,
        }
    ]
    assert dao.fail_expired_queued_calls == []
    assert dao.reconcile_expired_execution_calls == []


def test_queued_reconciler_only_marks_expired_queued(monkeypatch) -> None:
    dao = FakeCeleryTaskDao()
    monkeypatch.setattr(reconciler, "settings", FakeSettings)
    monkeypatch.setattr(reconciler, "new_celery_task_dao", lambda: dao)
    monkeypatch.setattr(reconciler, "run_in_async", _run_async)

    result = reconciler.fail_expired_queued.run()

    assert result == {"queued_failed_count": 1}
    assert dao.fail_expired_queued_calls == [
        {"batch_size": FakeSettings.CELERY_RECONCILER_BATCH_SIZE}
    ]
    assert dao.fail_stale_submitting_calls == []
    assert dao.reconcile_expired_execution_calls == []


def test_execution_reconciler_only_marks_expired_execution(monkeypatch) -> None:
    dao = FakeCeleryTaskDao()
    monkeypatch.setattr(reconciler, "settings", FakeSettings)
    monkeypatch.setattr(reconciler, "new_celery_task_dao", lambda: dao)
    monkeypatch.setattr(reconciler, "run_in_async", _run_async)

    result = reconciler.reconcile_expired_execution.run()

    assert result == {"execution_reconciled_count": 3}
    assert dao.reconcile_expired_execution_calls == [
        {"batch_size": FakeSettings.CELERY_RECONCILER_BATCH_SIZE}
    ]
    assert dao.fail_stale_submitting_calls == []
    assert dao.fail_expired_queued_calls == []


def test_scheduler_registers_all_state_machine_reconcilers() -> None:
    expected = {
        "celery_task_submitting_reconciler": SUBMITTING_RECONCILER_TASK_NAME,
        "celery_task_queued_reconciler": QUEUED_RECONCILER_TASK_NAME,
        "celery_task_execution_reconciler": EXECUTION_RECONCILER_TASK_NAME,
    }

    for schedule_name, task_name in expected.items():
        assert scheduler.CELERY_TASK_ROUTES[task_name] == {
            "queue": scheduler.settings.CELERY_RECONCILER_QUEUE
        }
        if scheduler.settings.CELERY_RECONCILER_BEAT_ENABLED:
            assert scheduler.STATIC_BEAT_SCHEDULE[schedule_name]["task"] == task_name
