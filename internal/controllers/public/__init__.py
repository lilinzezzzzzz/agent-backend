from fastapi import APIRouter

from internal.controllers.public import logger_span, test

router = APIRouter(prefix="/v1/public")

routers = [logger_span.router, test.router]

for r in routers:
    router.include_router(router=r)
