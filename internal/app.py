from contextlib import asynccontextmanager

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
from internal.utils.snowflake import init_snowflake_id_generator
from pkg.logger import init_logger, logger
from pkg.tracing import init_tracing, shutdown_tracing


def create_app() -> FastAPI:
    debug = settings.DEBUG
    app = FastAPI(
        debug=debug,
        docs_url="/docs" if debug else None,
        redoc_url="/redoc" if debug else None,
        lifespan=lifespan,
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
    # 6. GZip 中间件：压缩响应，提高传输效率
    from starlette.middleware.gzip import GZipMiddleware

    app.add_middleware(GZipMiddleware)

    # 4. 认证中间件：校验 Token，确保只有合法用户访问 API
    from internal.middlewares.auth import ASGIAuthMiddleware

    app.add_middleware(ASGIAuthMiddleware)

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
        )

    # 1. 日志中间件：记录请求和响应的日志，监控 API 性能和请求流
    from internal.middlewares import ASGIRecordMiddleware

    app.add_middleware(ASGIRecordMiddleware)


# 定义 lifespan 事件处理器
@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 初始化配置（必须最先执行）
    init_settings()

    # 初始化日志（使用配置中的格式）
    init_logger(
        log_format=settings.LOG_FORMAT,
        base_log_dir=settings.LOG_BASE_DIR,
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
    # 初始化 DB（使用配置中的 echo）
    init_async_db(echo=settings.DB_ECHO)
    # 初始化 Redis
    init_async_redis()
    # 初始化签名认证
    init_signature_auth_handler()
    # 初始化 Snowflake ID Generator
    init_snowflake_id_generator()
    # 初始化后台任务管理器
    await init_background_task_manager()

    logger.info("lifespan init completed, Application will start.")

    yield

    # 关闭时的清理逻辑
    await close_async_db()
    await close_async_redis()
    await close_background_task_manager()
    logger.warning("Application is about to close.")
    shutdown_tracing()
