"""Celery 客户端配置与生命周期管理

职责：
- Celery 客户端初始化（celery_app, celery_client）
- Worker 生命周期钩子（启动/关闭）
- 异步代码执行辅助（run_in_async）
- 健康检查（check_celery_health）

任务定义在 internal/tasks/ 目录，此处仅负责 Celery 基础设施集成。
"""

from collections.abc import Callable, Coroutine
import anyio
from celery import Celery

from internal.config import init_settings, settings
from internal.infra.database import close_async_db, init_async_db, reset_async_db
from internal.infra.redis import close_async_redis, init_async_redis, reset_async_redis
from internal.tasks.scheduler import (
    CELERY_INCLUDE_MODULES,
    CELERY_TASK_ROUTES,
    STATIC_BEAT_SCHEDULE,
)
from pkg.logger import init_logger, logger
from pkg.tracing import init_tracing, shutdown_tracing
from pkg import request_context as context
from pkg.celery_queue import CeleryClient

__all__ = [
    "celery_app",
    "celery_client",
    "run_in_async",
    "check_celery_health",
]


# =========================================================
# 2. Worker 生命周期钩子 (资源管理)
# =========================================================


def _worker_startup():
    """
    [Startup Hook] Worker 进程启动时执行：初始化基础资源
    注意：数据库和 Redis 连接在 run_in_async 中按需初始化
    """
    # 1. 首先初始化配置 (其他组件可能依赖配置)
    try:
        init_settings()
    except Exception as e:
        raise RuntimeError("Worker configuration initialization failed") from e

    # 2. 然后初始化 Logger (依赖配置中的日志格式等)
    init_logger(
        level="INFO",
        log_format=settings.LOG_FORMAT,
        base_log_dir=settings.LOG_BASE_DIR / "celery",
        write_to_file=settings.LOG_WRITE_TO_FILE,
        write_to_console=settings.LOG_WRITE_TO_CONSOLE,
    )
    init_tracing(
        enabled=settings.OTEL_TRACING_ENABLED,
        service_name=settings.OTEL_SERVICE_NAME,
        otlp_traces_endpoint=(
            str(settings.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT)
            if settings.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT is not None
            else None
        ),
        exporter_timeout_seconds=settings.OTEL_EXPORTER_OTLP_TIMEOUT_SECONDS,
    )
    logger.info(f"Celery worker initialized with APP_ENV: {settings.APP_ENV}")
    logger.info(">>> Worker Process Starting: Initializing basic resources...")


async def _worker_shutdown():
    """
    [Shutdown Hook] Worker 进程关闭时执行：基础资源清理
    注意：数据库和 Redis 连接在 run_in_async 中已自动清理
    """
    logger.warning("Worker Process Stopping: Cleaning up basic resources...")
    shutdown_tracing()


# =========================================================
# 3. 实例化 Celery 客户端 (Global Singleton)
# =========================================================

# 必须在模块层级直接实例化，确保 'celery -A ...' 命令行能找到
celery_client = CeleryClient(
    app_name="my_fastapi_server",
    broker_url=settings.redis_url,
    backend_url=settings.redis_url,
    # 注册模块与路由
    include=CELERY_INCLUDE_MODULES,
    task_routes=CELERY_TASK_ROUTES,
    beat_schedule=STATIC_BEAT_SCHEDULE,
)

# 注册生命周期钩子
celery_client.register_worker_hooks(
    on_startup=_worker_startup, on_shutdown=_worker_shutdown
)

# 导出原生 App 对象供 Celery CLI 使用
celery_app: Celery = celery_client.app

# =========================================================
# 4. FastAPI 集成辅助函数
# =========================================================


def check_celery_health():
    """
    在 FastAPI Lifespan 中调用。
    用于检查配置加载情况，或测试 Broker 连通性。
    """
    logger.info("Initializing Celery integration...")
    logger.info(f"Celery Modules Included: {CELERY_INCLUDE_MODULES}")

    # 调试模式下可打印路由表
    logger.info(f"Celery Routes: {CELERY_TASK_ROUTES}")

    try:
        # 主动检测 Broker 连接 (Health Check)
        with celery_app.connection_or_acquire() as conn:
            conn.ensure_connection(max_retries=1)
        logger.info(f"Celery Broker ({settings.redis_url}) connected successfully.")
    except Exception as e:
        # 即使连不上也不要阻断 API 启动，只是记录错误，因为 Worker 是独立进程
        logger.error(f"Celery Broker connection failed: {e}")


def run_in_async[T](
    coro_func: Callable[[], Coroutine[None, None, T]], trace_id: str
) -> T:
    """
    在 Celery 同步任务中执行异步代码。
    """

    # 1. 重置旧的连接池
    reset_async_db()
    reset_async_redis()

    async def _wrapper() -> T:
        # 1. 在新事件循环中初始化（使用配置中的 echo 参数）
        init_async_db(echo=settings.DB_ECHO)
        init_async_redis()

        # 2. 设置上下文
        context.init(trace_id=trace_id)

        try:
            # 3. 执行业务逻辑 (通过闭包捕获 coro_func)
            return await coro_func()
        finally:
            # 4. 清理连接
            await close_async_db()
            await close_async_redis()

    return anyio.run(_wrapper)


"""
# =========================================================
# 启动命令说明（项目根目录执行）
# =========================================================
1. 启动任务
# 开发环境 - 基础启动
celery -A internal.infra.celery:celery_app worker -l info -c 1 -Q default,celery_queue

# 开发环境 - 指定并发数（容器资源有限时建议限制）
celery -A internal.infra.celery:celery_app worker -l info -c 2 -Q default,celery_queue

# 生产环境 - 推荐配置
celery -A internal.infra.celery:celery_app worker \
    -l info \
    -c 4 \
    --max-tasks-per-child 1000 \
    --max-memory-per-child 120000 \
    -Q default,celery_queue

# 2. 启动 Beat (派发定时任务):
# celery -A internal.infra.celery:celery_app beat -l info
"""
