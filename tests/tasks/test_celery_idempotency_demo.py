import anyio
import pytest
from celery.exceptions import Ignore

from internal.tasks import celery_idempotency_demo


class FakeExecutionService:
    def __init__(
        self, *, claimed: bool, cancellation_checks: list[bool] | None = None
    ) -> None:
        self.claimed = claimed
        self.cancellation_checks = cancellation_checks or [False, False]
        self.claim_calls: list[dict] = []
        self.succeed_calls: list[dict] = []
        self.cancellation_calls: list[dict] = []

    async def claim(self, **kwargs) -> bool:
        self.claim_calls.append(kwargs)
        return self.claimed

    async def acknowledge_cancellation(self, **kwargs) -> bool:
        self.cancellation_calls.append(kwargs)
        return self.cancellation_checks.pop(0)

    async def succeed(self, **kwargs) -> bool:
        self.succeed_calls.append(kwargs)
        return True

    async def fail(self, **_kwargs) -> bool:
        raise AssertionError("加法 demo 不应进入失败分支")


def _run_async(coro_func, *, trace_id: str):
    assert isinstance(trace_id, str)
    assert trace_id
    return anyio.run(coro_func)


class _FakeTaskRequest:
    def __init__(self, headers: dict[str, object] | None) -> None:
        self.headers = headers


class _FakeTask:
    def __init__(self, headers: dict[str, object] | None) -> None:
        self.request = _FakeTaskRequest(headers)


def test_resolve_trace_id_uses_caller_header() -> None:
    trace_id = celery_idempotency_demo._resolve_trace_id(
        _FakeTask(headers={"trace_id": "caller-trace-id"})
    )

    assert trace_id == "caller-trace-id"


@pytest.mark.parametrize("headers", [None, {}, {"trace_id": ""}, {"trace_id": 123}])
def test_resolve_trace_id_rejects_missing_or_invalid_header(
    headers: dict[str, object] | None,
) -> None:
    with pytest.raises(ValueError, match="missing required trace_id header"):
        celery_idempotency_demo._resolve_trace_id(_FakeTask(headers=headers))


def test_sum_numbers_claims_before_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    service = FakeExecutionService(claimed=True)
    monkeypatch.setattr(
        celery_idempotency_demo,
        "new_celery_task_service",
        lambda: service,
    )
    monkeypatch.setattr(
        celery_idempotency_demo,
        "_resolve_trace_id",
        lambda _task: "caller-trace-id",
    )
    monkeypatch.setattr(celery_idempotency_demo, "run_in_async", _run_async)

    result = celery_idempotency_demo.sum_numbers.run(123, "user:1", 2, 3)

    assert result == {"x": 2, "y": 3, "result": 5}
    assert service.claim_calls[0]["record_id"] == 123
    assert service.claim_calls[0]["scope"] == "user:1"
    assert service.claim_calls[0]["execution_token"]
    assert service.succeed_calls[0]["record_id"] == 123
    assert len(service.cancellation_calls) == 2


def test_sum_numbers_ignores_duplicate_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FakeExecutionService(claimed=False)
    monkeypatch.setattr(
        celery_idempotency_demo,
        "new_celery_task_service",
        lambda: service,
    )
    monkeypatch.setattr(
        celery_idempotency_demo,
        "_resolve_trace_id",
        lambda _task: "caller-trace-id",
    )
    monkeypatch.setattr(celery_idempotency_demo, "run_in_async", _run_async)

    with pytest.raises(Ignore):
        celery_idempotency_demo.sum_numbers.run(123, "user:1", 2, 3)

    assert service.succeed_calls == []
    assert service.cancellation_calls == []


def test_sum_numbers_cooperatively_stops_when_cancelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FakeExecutionService(claimed=True, cancellation_checks=[True])
    monkeypatch.setattr(
        celery_idempotency_demo,
        "new_celery_task_service",
        lambda: service,
    )
    monkeypatch.setattr(
        celery_idempotency_demo,
        "_resolve_trace_id",
        lambda _task: "caller-trace-id",
    )
    monkeypatch.setattr(celery_idempotency_demo, "run_in_async", _run_async)

    with pytest.raises(Ignore):
        celery_idempotency_demo.sum_numbers.run(123, "user:1", 2, 3)

    assert len(service.cancellation_calls) == 1
    assert service.succeed_calls == []
