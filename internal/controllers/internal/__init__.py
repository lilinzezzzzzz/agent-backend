from fastapi import APIRouter

from internal.controllers.internal import rag

router = APIRouter(prefix="/v1/internal")

routers = [
    rag.router,
]

for r in routers:
    router.include_router(router=r)
