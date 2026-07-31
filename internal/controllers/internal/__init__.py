from fastapi import APIRouter, Depends

from internal.controllers.internal import rag
from internal.dependencies.auth import require_internal_signature

router = APIRouter(
    prefix="/v1/internal",
    dependencies=[Depends(require_internal_signature)],
)

routers = [
    rag.router,
]

for r in routers:
    router.include_router(router=r)
