from fastapi import APIRouter

from internal.controllers.api import agent, auth, celery_task, logger_span, rag, user

router = APIRouter(prefix="/v1")

routers = [
    agent.router,
    auth.router,
    celery_task.router,
    logger_span.router,
    rag.router,
    user.router,
]

for r in routers:
    router.include_router(router=r)
