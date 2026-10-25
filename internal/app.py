import sys
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

import anyio
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

from internal.config import init_settings, settings
from internal.infra.database import close_async_db, init_async_db
from internal.infra.redis import close_async_redis, init_async_redis
from internal.utils.background_tasks import (
    close_background_task_manager,
    init_background_task_manager,
)
from internal.utils.signature import init_signature_auth_handler
from pkg.logger import init_logger, logger
from pkg.tracing import init_tracing, shutdown_tracing


def create_app() -> FastAPI:
    debug = settings.DEBUG
    app = FastAPI(
        debug=debug,
        docs_url="/docs" if debug else None,
        redoc_url="/redoc" if debug else None,
        lifespan=lifespan,
        telemetry={
            "tracing": settings.OTEL_TRACING_ENABLED,
            "operation_spans": settings.OTEL_OPERATION_SPANS_ENABLED,
            "metrics": settings.OTEL_METRICS_ENABLED,
            "logs": settings.OTEL_LOGS_ENABLED,
            # Settings 校验当前自管 exporter 模式只允许关闭自动配置。
            "auto_configure": settings.OTEL_AUTO_CONFIGURE,
        },
    )

    register_router(app)
    register_exception(app)
    register_middleware(app)

    return app


def register_router(app: FastAPI):
    from internal.controllers import api

    app.include_router(api.router)
    from internal.controllers import internal

    app.include_router(internal.router)
    from internal.controllers import public

    app.include_router(public.router)


def register_exception(app: FastAPI):
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_: Request, exc: RequestValidationError):
        # 重新抛出异常，让外层中间件统一处理
        raise exc


def register_middleware(app: FastAPI):
    # 4. GZip 中间件：压缩响应，提高传输效率
    from starlette.middleware.gzip import GZipMiddleware

    app.add_middleware(GZipMiddleware)

    # 3. Endpoint Guard 中间件：配置化禁用指定 endpoint，避免进入认证和业务处理
    from internal.middlewares.endpoint_guard import ASGIEndpointGuardMiddleware

    app.add_middleware(ASGIEndpointGuardMiddleware)

    # 2. CORS 中间件：处理跨域请求
    if settings.BACKEND_CORS_ORIGINS:
        from starlette.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_credentials=True,
            allow_origins=settings.BACKEND_CORS_ORIGINS,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["traceparent"],
        )

    # 1. 日志中间件：记录请求和响应的日志，监控 API 性能和请求流
    from internal.middlewares import ASGIRecordMiddleware

    app.add_middleware(
        ASGIRecordMiddleware,
        tracing_enabled=settings.OTEL_TRACING_ENABLED,
        logs_enabled=settings.OTEL_LOGS_ENABLED,
    )


# 定义 lifespan 事件处理器
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    # 初始化配置（必须最先执行）
    init_settings()

    # 初始化日志（使用配置中的格式）
    init_logger(
        log_format=settings.LOG_FORMAT,
        base_log_dir=settings.LOG_BASE_DIR,
        write_to_file=settings.LOG_WRITE_TO_FILE,
        write_to_console=settings.LOG_WRITE_TO_CONSOLE,
    )
    cleanup = AsyncExitStack()
    cleanup.push_async_callback(logger.complete)
    try:
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
        cleanup.callback(shutdown_tracing)

        # 先登记清理，覆盖初始化中途已创建部分连接的失败路径。
        cleanup.push_async_callback(close_async_db)
        init_async_db(echo=settings.DB_ECHO)
        cleanup.push_async_callback(close_async_redis)
        init_async_redis()
        init_signature_auth_handler()

        # 后台 TaskGroup 必须由创建它的任务退出，连接清理随后交给受保护的子任务。
        async with AsyncExitStack() as background_cleanup:
            background_cleanup.push_async_callback(close_background_task_manager)
            await init_background_task_manager()
            logger.info("lifespan init completed, Application will start.")
            try:
                yield
            finally:
                logger.warning("Application is about to close.")
    finally:
        await _close_resources(cleanup)


async def _close_resources(cleanup: AsyncExitStack) -> None:
    """等待连接和日志清理完成，再原样传播清理异常或调用方的取消。"""
    caller_exc_info = sys.exc_info()
    caller_error = caller_exc_info[1]
    cleanup_error: BaseException | None = None

    async def close() -> None:
        nonlocal cleanup_error
        try:
            try:
                if caller_error is not None:
                    raise caller_error
            finally:
                # 保留调用方异常状态，让 ExitStack 正确连接多次清理失败的上下文。
                with anyio.CancelScope(shield=True):
                    await cleanup.__aexit__(*caller_exc_info)
        except BaseException as exc:
            # 交回调用方抛出，避免 TaskGroup 把清理异常包装为 ExceptionGroup。
            if exc is not caller_error:
                cleanup_error = exc

    try:
        with anyio.CancelScope(shield=True):
            async with anyio.create_task_group() as task_group:
                task_group.start_soon(close)
    finally:
        if cleanup_error is not None:
            fixed_context = cleanup_error.__context__
            try:
                raise cleanup_error
            except BaseException:
                if fixed_context is not None:
                    cleanup_error.__context__ = fixed_context
                raise
