"""复用标准应用装配，在独立进程中额外挂载实验性路由。"""

from fastapi import FastAPI

from examples.api.routes import router
from internal.app import create_app


def create_demo_app() -> FastAPI:
    app = create_app()
    app.include_router(router, prefix="/v1/public")
    return app
