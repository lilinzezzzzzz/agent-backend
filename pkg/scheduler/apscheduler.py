from collections import deque
from collections.abc import Callable
from typing import Any

from apscheduler.job import Job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from pkg.logger import logger


class ApsSchedulerManager:
    """
    AsyncIO APScheduler 封装管理器。

    特性：
    1. 延迟启动：支持在 start() 之前注册任务，启动时自动加载。
    2. 全局配置：统一管理 jitter, max_instances 等默认参数。
    3. 简便封装：提供 Cron/Interval/Date 的快捷注册方法。
    """

    def __init__(
        self,
        *,
        timezone: str = "UTC",
        max_instances: int = 50,
        default_jitter: int | None = 5,
        default_coalesce: bool = True,
        default_misfire_grace_time: int = 60,
        extra_config: dict[str, Any] | None = None,
    ) -> None:
        """
        :param timezone: 时区，如 "Asia/Shanghai" 或 "UTC"
        :param max_instances: 单个任务最大并发实例数
        :param default_jitter: 默认抖动时间（秒），防止整点并发高峰
        :param default_coalesce: 积压任务是否合并执行
        :param default_misfire_grace_time: 错过执行时间的宽限期（秒）
        :param extra_config: 传递给 Scheduler 的其他配置
        """
        # 1. 组装 APScheduler 原生支持的全局 job_defaults
        # 这样就不需要在每次 add_job 时手动填充了
        job_defaults = {
            "coalesce": default_coalesce,
            "max_instances": max_instances,
            "misfire_grace_time": default_misfire_grace_time,
        }

        # 2. 初始化调度器
        self._scheduler = AsyncIOScheduler(
            timezone=timezone,
            job_defaults=job_defaults,
            **(extra_config or {})
        )

        # 3. 记录自定义的全局默认值 (APScheduler job_defaults 不支持全局 jitter，需手动处理)
        self._default_jitter = default_jitter

        self._started: bool = False
        # 待处理任务队列: (func, args, kwargs)
        self._pending_jobs: deque[tuple[Callable, tuple, dict]] = deque()

    # ------------------------- 生命周期管理 -------------------------

    def start(self) -> None:
        """启动调度器，并加载所有积压的任务。"""
        if self._started:
            return

        logger.info(f"Starting APScheduler... (Pending jobs: {len(self._pending_jobs)})")

        # 1. 先启动调度器
        try:
            self._scheduler.start()
            self._started = True
        except Exception as e:
            logger.critical(f"Failed to start APScheduler: {e}")
            raise e

        # 2. 再加载积压的任务 (防止 start 失败导致任务丢失，或 start 过程中事件循环未就绪)
        while self._pending_jobs:
            func, args, kwargs = self._pending_jobs.popleft()
            try:
                self._add_job_internal(func, *args, **kwargs)
            except Exception as e:
                logger.error(f"Failed to load pending job {kwargs.get('id')}: {e}")

        logger.info("APScheduler started successfully.")

    async def shutdown(self, wait: bool = True) -> None:
        """关闭调度器。"""
        if not self._started:
            return

        logger.info("Shutting down APScheduler...")
        try:
            self._scheduler.shutdown(wait=wait)
        except Exception as e:
            logger.error(f"Error during APScheduler shutdown: {e}")
        finally:
            self._started = False
            logger.info("APScheduler stopped.")

    # ------------------------- 核心注册逻辑 -------------------------

    def _add_job_internal(self, func: Callable, *args: Any, **kwargs: Any) -> Job:
        """内部方法：直接向 scheduler 添加任务"""
        # 注入全局默认 jitter (如果任务未指定)
        if self._default_jitter is not None and "jitter" not in kwargs:
            kwargs["jitter"] = self._default_jitter

        job = self._scheduler.add_job(func, *args, **kwargs)
        logger.info(f"Job added: id={job.id} trigger={job.trigger} next_run={job.next_run_time}")
        return job

    def _register_job(self, func: Callable[..., Any], trigger: Any = None, **kwargs: Any) -> str:
        """
        通用注册方法 (add_job 的封装)。
        如果 Scheduler 未启动，先加入队列；否则立即添加。
        """
        job_id = kwargs.get("id", func.__name__)
        kwargs.setdefault("id", job_id)

        if trigger:
            kwargs["trigger"] = trigger

        if self._started:
            self._add_job_internal(func, **kwargs)
        else:
            self._pending_jobs.append((func, (), kwargs))
            logger.info(f"Job registered (pending): id={job_id}")

        return job_id

    # ------------------------- 快捷注册方法 -------------------------

    def register_cron(
        self,
        func: Callable,
        *,
        job_id: str | None = None,
        replace_existing: bool = True,
        **cron_args: Any
    ) -> str:
        """
        注册 Cron 任务 (类 Linux Crontab)。
        用法: register_cron(my_func, minute='*/5', hour='8-18')
        """
        trigger = CronTrigger(**cron_args)
        return self._register_job(
            func,
            trigger=trigger,
            id=job_id or func.__name__,
            replace_existing=replace_existing
        )

    def register_interval(
        self,
        func: Callable,
        *,
        job_id: str | None = None,
        replace_existing: bool = True,
        seconds: int = 0,
        minutes: int = 0,
        hours: int = 0,
        days: int = 0,
        **job_options: Any
    ) -> str:
        """
        注册间隔任务。
        用法: register_interval(my_func, seconds=30)
        """
        trigger = IntervalTrigger(days=days, hours=hours, minutes=minutes, seconds=seconds)
        return self._register_job(
            func,
            trigger=trigger,
            id=job_id or func.__name__,
            replace_existing=replace_existing,
            **job_options
        )

    def register_date(
        self,
        func: Callable,
        run_date: str | Any,
        *,
        job_id: str | None = None,
        replace_existing: bool = True,
        **job_options: Any
    ) -> str:
        """
        注册一次性任务。
        用法: register_date(my_func, run_date='2023-12-01 12:00:00')
        """
        trigger = DateTrigger(run_date=run_date)
        return self._register_job(
            func,
            trigger=trigger,
            id=job_id or func.__name__,
            replace_existing=replace_existing,
            **job_options
        )

    # ------------------------- 管理与查询 -------------------------

    @property
    def scheduler(self) -> AsyncIOScheduler:
        """暴露底层 Scheduler 以便使用高级功能"""
        return self._scheduler

    def get_job(self, job_id: str) -> Job | None:
        return self._scheduler.get_job(job_id)

    def get_jobs(self) -> list[Job]:
        return self._scheduler.get_jobs()

    def remove_job(self, job_id: str) -> None:
        """移除任务（静默处理不存在的情况）"""
        try:
            self._scheduler.remove_job(job_id)
            logger.info(f"Removed job: id={job_id}")
        except Exception as e:
            logger.warning(f"Attempted to remove non-existent job: {job_id}, error: {e}")

    def pause_job(self, job_id: str) -> None:
        self._scheduler.pause_job(job_id)
        logger.info(f"Paused job: id={job_id}")

    def resume_job(self, job_id: str) -> None:
        self._scheduler.resume_job(job_id)
        logger.info(f"Resumed job: id={job_id}")

    def modify_job(self, job_id: str, **changes) -> None:
        """修改运行中任务的属性"""
        self._scheduler.modify_job(job_id, **changes)
        logger.info(f"Modified job: id={job_id}, changes={changes}")

# ------------------------- 用法示例 -------------------------
# from datetime import datetime, timedelta
# tool = new_aps_scheduler_tool(
#     timezone="UTC",
#     max_instances=50,
#     # scheduler 层面的默认（没传就用下方默认）：
#     # job_defaults={"coalesce": True, "misfire_grace_time": 60},
#     # 类级默认（单个任务可覆盖）：
#     default_jitter=7,                  # 全局抖动 ±7 秒
#     default_coalesce=True,             # 合并滞后触发
#     default_misfire_grace_time=60,     # 允许 60 秒内补跑
# )
# tool.register_cron(my_task, minute="*", second=0, jitter=10)  # 覆盖为 ±10 秒
# tool.register_interval_job(heartbeat, interval_kwargs={"seconds": 30})
# tool.register_date_job(one_shot, date_kwargs={"run_date": datetime.utcnow() + timedelta(minutes=1)}, coalesce=True)
# tool.start()
