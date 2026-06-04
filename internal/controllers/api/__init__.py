from fastapi import APIRouter

from internal.controllers.api import agent, auth, user

router = APIRouter(prefix="/v1")

routers = [
    agent.router,
    auth.router,
    user.router,
]

for r in routers:
    router.include_router(router=r)
