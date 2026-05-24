from pydantic import BaseModel, ConfigDict, Field


class OrmModel(BaseModel):
    """支持从 ORM 对象转换的基类。"""

    model_config = ConfigDict(from_attributes=True)


class BaseResponse[T](BaseModel):
    """基础响应模型

    用法:
        # 定义响应schema
        class UserInfo(BaseModel):
            id: int
            name: str

        # 在controller中使用
        @router.get("/user", response_model=BaseResponse[UserInfo])
        async def get_user():
            return BaseResponse(
                code=20000,
                message="success",
                data=UserInfo(id=1, name="张三")
            )

        # 错误响应
        return BaseResponse(code=40000, message="参数错误", data=None)
    """

    code: int = 20000
    message: str = ""
    data: T | None = None


class PageData[T](BaseModel):
    """分页数据载荷。"""

    items: list[T] = Field(default_factory=list)
    page: int = 1
    limit: int = 10
    total: int = 0


class BaseListResponse[T](BaseModel):
    """基础列表响应模型

    用法:
        # 定义列表项schema
        class UserInfo(BaseModel):
            id: int
            name: str

        # 在controller中使用
        @router.get("/users", response_model=BaseListResponse[UserInfo])
        async def list_users(page: int = 1, limit: int = 10):
            users = [UserInfo(id=1, name="张三"), UserInfo(id=2, name="李四")]
            return BaseListResponse(
                code=20000,
                message="success",
                data=PageData(items=users, page=page, limit=limit, total=100)
            )
    """

    code: int = 20000
    message: str = ""
    data: PageData[T] | None = None
