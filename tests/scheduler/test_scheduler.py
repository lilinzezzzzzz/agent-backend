from apscheduler.triggers.cron import CronTrigger

from pkg.logger import init_logger
from pkg.scheduler import ApsSchedulerManager


def test_register_cron_accepts_cron_fields_before_start() -> None:
    init_logger(write_to_file=False, write_to_console=False)
    manager = ApsSchedulerManager()

    def job() -> None:
        return None

    job_id = manager.register_cron(job, minute="*/15", second=0)

    assert job_id == "job"
    assert len(manager._pending_jobs) == 1
    _, _, kwargs = manager._pending_jobs[0]
    assert isinstance(kwargs["trigger"], CronTrigger)
