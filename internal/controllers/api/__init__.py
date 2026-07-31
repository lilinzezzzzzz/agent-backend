from fastapi import APIRouter

from internal.controllers.api import agent, auth, celery_task, logger_span, rag, user
from internal.dependencies.auth import AuthenticatedUserDependency

router = APIRouter(prefix="/v1")
authenticated_router = APIRouter(
    dependencies=[AuthenticatedUserDependency],
)

authenticated_routers = [
    agent.router,
    celery_task.router,
    logger_span.router,
    rag.router,
    user.router,
]

router.include_router(auth.anonymous_router)
router.include_router(auth.authenticated_router)
for child_router in authenticated_routers:
    authenticated_router.include_router(child_router)
router.include_router(authenticated_router)
