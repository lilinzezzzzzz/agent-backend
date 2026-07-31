from fastapi import APIRouter

from internal.dependencies.user import UserServiceDep
from internal.schemas import BaseResponse
from pkg.api_response import ResponsePayload, success_response

router = APIRouter(prefix="/user", tags=["api user"])


@router.get("/hello-world", response_model=BaseResponse[None], summary="用户 Hello World")
async def hello_world(service: UserServiceDep) -> ResponsePayload:
    await service.hello_world()
    return success_response()
