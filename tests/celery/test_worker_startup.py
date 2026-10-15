import os
import runpy
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from celery.bin.base import CLIContext
from celery.bin.worker import worker

import internal.config as config_module
from internal.infra.celery.application import celery_app
from internal.tasks import scheduler

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKER_SCRIPT = PROJECT_ROOT / "entrypoints" / "run_celery_worker.sh"


def test_app_subscribes_to_all_configured_routes_and_beat_queues() -> None:
    expected = {celery_app.conf.task_default_queue}
    expected.update(route["queue"] for route in celery_app.conf.task_routes.values())
    expected.update(
        entry["options"]["queue"]
        for entry in celery_app.conf.beat_schedule.values()
        if entry.get("options", {}).get("queue")
    )
    assert set(celery_app.amqp.queues) == expected
    for name, queue in celery_app.amqp.queues.items():
        assert queue.exchange.name == name
        assert queue.exchange.type == "direct"
        assert queue.routing_key == name


@pytest.mark.parametrize("maintenance_queue", ["custom_maintenance", "celery_queue"])
@pytest.mark.parametrize("beat_enabled", [True, False])
def test_worker_queues_follow_settings_and_deduplicate(
    maintenance_queue: str,
    beat_enabled: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config_module,
        "settings",
        SimpleNamespace(
            CELERY_RECONCILER_QUEUE=maintenance_queue,
            CELERY_RECONCILER_BEAT_ENABLED=beat_enabled,
            CELERY_PUBLISH_RECONCILER_INTERVAL_SECONDS=10,
            CELERY_RECONCILER_INTERVAL_SECONDS=60,
        ),
    )
    # Evaluate scheduling configuration independently without mutating the app's copy.
    configuration = runpy.run_path(scheduler.__file__)
    queues = configuration["get_worker_queue_names"]()
    assert set(queues) == {"default", "celery_queue", "cron_queue", maintenance_queue}
    assert len(queues) == len(set(queues))
    assert queues[0] == configuration["CELERY_DEFAULT_QUEUE"]
    routes = configuration["CELERY_TASK_ROUTES"]
    assert all(route["queue"] in queues for route in routes.values())
    for entry in configuration["STATIC_BEAT_SCHEDULE"].values():
        if entry.get("options", {}).get("queue"):
            assert entry["options"]["queue"] in queues


def test_worker_queues_include_beat_only_destinations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scheduler, "CELERY_DEFAULT_QUEUE", "custom_default")
    monkeypatch.setattr(
        scheduler,
        "CELERY_TASK_ROUTES",
        {"business.*": {"queue": "business_queue"}},
    )
    monkeypatch.setattr(
        scheduler,
        "STATIC_BEAT_SCHEDULE",
        {
            "scheduled": {"task": "scheduled.task", "options": {"queue": "beat_only"}},
            "default": {"task": "default.task"},
        },
    )
    assert scheduler.get_worker_queue_names() == (
        "custom_default",
        "business_queue",
        "beat_only",
    )


@pytest.fixture
def invoke_worker_script(
    tmp_path: Path,
) -> Callable[..., tuple[subprocess.CompletedProcess[str], list[str]]]:
    capture_path = tmp_path / "arguments.txt"
    uv_stub = tmp_path / "uv"
    uv_stub.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$PWD" "$@" > "$WORKER_ARGUMENTS_FILE"\n'
        'exit "${WORKER_EXIT_CODE:-0}"\n',
        encoding="utf-8",
    )
    uv_stub.chmod(0o755)

    def invoke(
        args: tuple[str, ...] = (),
        queues: str | None = None,
        exit_code: int = 0,
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        environment = os.environ.copy()
        for name in ("CELERY_QUEUES", "CELERY_LOG_LEVEL", "CELERY_CONCURRENCY"):
            environment.pop(name, None)
        environment.update(
            PATH=f"{tmp_path}{os.pathsep}{environment.get('PATH', '')}",
            WORKER_ARGUMENTS_FILE=str(capture_path),
            WORKER_EXIT_CODE=str(exit_code),
            CELERY_LOG_LEVEL="warning",
            CELERY_CONCURRENCY="8",
        )
        if queues is not None:
            environment["CELERY_QUEUES"] = queues
        result = subprocess.run(
            ["/bin/bash", str(WORKER_SCRIPT), *args],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        return result, capture_path.read_text(encoding="utf-8").splitlines()

    return invoke


@pytest.mark.parametrize("queues", [None, ""])
def test_script_uses_app_queue_configuration_by_default(
    queues: str | None,
    invoke_worker_script: Callable,
) -> None:
    result, captured = invoke_worker_script(
        args=("--max-tasks-per-child=1000",), queues=queues
    )
    assert result.returncode == 0
    assert captured == [
        str(PROJECT_ROOT),
        "run",
        "celery",
        "-A",
        "internal.infra.celery.application:celery_app",
        "worker",
        "-l",
        "warning",
        "-c",
        "8",
        "--pool",
        "prefork",
        "--max-tasks-per-child=1000",
    ]


@pytest.mark.parametrize(
    ("environment_queues", "arguments", "expected"),
    [
        ("dedicated_a,dedicated_b", (), ["dedicated_a", "dedicated_b"]),
        (None, ("-Q", "cli_queue"), ["cli_queue"]),
        ("environment_queue", ("--queues=cli_queue",), ["cli_queue"]),
    ],
)
def test_explicit_queue_selection_is_preserved(
    environment_queues: str | None,
    arguments: tuple[str, ...],
    expected: list[str],
    invoke_worker_script: Callable,
) -> None:
    result, captured = invoke_worker_script(args=arguments, queues=environment_queues)
    assert result.returncode == 0
    worker_arguments = captured[captured.index("worker") + 1 :]
    context = CLIContext(app=celery_app, no_color=True, workdir=str(PROJECT_ROOT))
    # Parse using Celery's real CLI without invoking the worker or contacting a broker.
    with click.Context(click.Command("celery"), obj=context) as parent:
        with worker.make_context("worker", worker_arguments, parent=parent) as parsed:
            assert parsed.params["queues"] == expected
            assert parsed.params["concurrency"] == 8


def test_script_preserves_process_exit_code(invoke_worker_script: Callable) -> None:
    result, _ = invoke_worker_script(exit_code=23)
    assert result.returncode == 23
