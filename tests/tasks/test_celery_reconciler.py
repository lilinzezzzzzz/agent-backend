import anyio

from internal.tasks import reconciler


class FakeSettings:
    CELERY_RECONCILER_BATCH_SIZE = 25
    CELERY_PUBLISH_CONFIRM_TIMEOUT_SECONDS = 30
    CELERY_PUBLISHED_ORPHAN_SECONDS = 900


class FakeCeleryTaskDao:
    def __init__(self) -> None:
        self.fail_stale_pending_publish_calls: list[dict[str, int]] = []
        self.reconcile_expired_running_calls: list[dict[str, int]] = []
        self.detect_orphaned_calls: list[dict[str, int]] = []

    async def fail_stale_pending_publish(self, **kwargs: int) -> list[int]:
        self.fail_stale_pending_publish_calls.append(kwargs)
        return [1, 2]

    async def reconcile_expired_running(self, **kwargs: int) -> list[int]:
        self.reconcile_expired_running_calls.append(kwargs)
        return [3]

    async def detect_orphaned(self, **kwargs: int) -> list[int]:
        self.detect_orphaned_calls.append(kwargs)
        return [4, 5, 6]


def _run_async(coro_func, *, trace_id: str):
    assert isinstance(trace_id, str)
    assert trace_id.startswith("celery-")
    return anyio.run(coro_func)


def test_publish_reconciler_only_marks_stale_pending_publish(
    monkeypatch,
) -> None:
    dao = FakeCeleryTaskDao()
    monkeypatch.setattr(reconciler, "settings", FakeSettings)
    monkeypatch.setattr(reconciler, "new_celery_task_dao", lambda: dao)
    monkeypatch.setattr(reconciler, "run_in_async", _run_async)

    result = reconciler.fail_stale_pending_publish.run()

    assert result == {"publish_failed_count": 2}
    assert dao.fail_stale_pending_publish_calls == [
        {
            "batch_size": FakeSettings.CELERY_RECONCILER_BATCH_SIZE,
            "stale_seconds": FakeSettings.CELERY_PUBLISH_CONFIRM_TIMEOUT_SECONDS,
        }
    ]
    assert dao.detect_orphaned_calls == []
    assert dao.reconcile_expired_running_calls == []


def test_running_reconciler_only_marks_expired_running(monkeypatch) -> None:
    dao = FakeCeleryTaskDao()
    monkeypatch.setattr(reconciler, "settings", FakeSettings)
    monkeypatch.setattr(reconciler, "new_celery_task_dao", lambda: dao)
    monkeypatch.setattr(reconciler, "run_in_async", _run_async)

    result = reconciler.reconcile_expired_running.run()

    assert result == {"reconciled_count": 1}
    assert dao.reconcile_expired_running_calls == [
        {"batch_size": FakeSettings.CELERY_RECONCILER_BATCH_SIZE}
    ]
    assert dao.fail_stale_pending_publish_calls == []
    assert dao.detect_orphaned_calls == []


def test_orphan_detector_uses_max_unclaimed_deadline(monkeypatch) -> None:
    dao = FakeCeleryTaskDao()
    monkeypatch.setattr(reconciler, "settings", FakeSettings)
    monkeypatch.setattr(reconciler, "new_celery_task_dao", lambda: dao)
    monkeypatch.setattr(reconciler, "run_in_async", _run_async)

    result = reconciler.detect_orphaned_tasks.run()

    assert result == {"orphaned_count": 3}
    assert dao.detect_orphaned_calls == [
        {
            "batch_size": FakeSettings.CELERY_RECONCILER_BATCH_SIZE,
            "orphan_seconds": FakeSettings.CELERY_PUBLISHED_ORPHAN_SECONDS,
        }
    ]
    assert dao.fail_stale_pending_publish_calls == []
    assert dao.reconcile_expired_running_calls == []
