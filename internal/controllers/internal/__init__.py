from fastapi import APIRouter

from internal.controllers.internal import rag
from internal.dependencies.auth import InternalSignatureDependency

router = APIRouter(
    prefix="/v1/internal",
    dependencies=[InternalSignatureDependency],
)

routers = [
    rag.router,
]

for r in routers:
    router.include_router(router=r)
