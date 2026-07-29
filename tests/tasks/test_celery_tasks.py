import pytest

from internal.infra.celery import celery_app
from internal.tasks import celery as celery_tasks
from internal.tasks.celery import number_sum
from pkg.logger import init_logger

init_logger(level="INFO")


class _FakeTaskRequest:
    def __init__(self, headers: dict[str, object] | None) -> None:
        self.headers = headers


class _FakeTask:
    def __init__(self, headers: dict[str, object] | None) -> None:
        self.request = _FakeTaskRequest(headers)


def test_resolve_task_trace_id_uses_caller_header() -> None:
    trace_id = celery_tasks._resolve_task_trace_id(
        _FakeTask(headers={"trace_id": "caller-trace-id"}),
    )

    assert trace_id == "caller-trace-id"


@pytest.mark.parametrize("headers", [None, {}, {"trace_id": ""}, {"trace_id": 123}])
def test_resolve_task_trace_id_rejects_missing_or_invalid_header(
    headers: dict[str, object] | None,
) -> None:
    with pytest.raises(ValueError, match="missing required trace_id header"):
        celery_tasks._resolve_task_trace_id(_FakeTask(headers=headers))


def test_number_sum_sync_execution() -> None:
    assert number_sum(10, 20) == 30


def test_celery_app_configuration() -> None:
    assert "internal.tasks.celery_tasks.number_sum" in celery_app.tasks
    assert celery_app.conf.task_default_queue == "default"
    assert celery_app.conf.timezone == "UTC"


def test_celery_task_routes() -> None:
    route = celery_app.conf.task_routes.get("internal.tasks.celery_tasks.number_sum")

    if route:
        assert isinstance(route, dict)


@pytest.mark.parametrize(
    "x,y,expected",
    [
        (1, 1, 2),
        (0, 0, 0),
        (-5, 5, 0),
        (100, -50, 50),
        (999, 1, 1000),
    ],
)
def test_number_sum_with_parameters(x: int, y: int, expected: int) -> None:
    assert number_sum(x, y) == expected
