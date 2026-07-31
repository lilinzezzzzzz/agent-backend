from typing import Annotated

from fastapi import Depends

from internal.services.user import UserService, new_user_service

UserServiceDep = Annotated[UserService, Depends(new_user_service)]
