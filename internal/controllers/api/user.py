from typing import Annotated

from fastapi import APIRouter, Depends

from internal.schemas import BaseResponse
from internal.services.user import UserService, new_user_service
from pkg.api_response import ResponsePayload, success_response

router = APIRouter(prefix="/user", tags=["api user"])


@router.get("/hello-world", response_model=BaseResponse[None], summary="用户 Hello World")
async def hello_world(
    service: Annotated[UserService, Depends(new_user_service)],
) -> ResponsePayload:
    await service.hello_world()
    return success_response()
