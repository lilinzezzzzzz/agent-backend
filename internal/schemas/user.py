from pydantic import BaseModel, Field

from internal.schemas import BaseListResponse


class UserLoginReqSchema(BaseModel):
    """用户登录请求"""

    username: str = Field(..., description="用户名", min_length=1, max_length=50)
    password: str = Field(..., description="密码", min_length=6, max_length=100)


class UserRegisterReqSchema(BaseModel):
    """用户注册请求"""

    username: str = Field(..., description="用户名", min_length=1, max_length=50)
    password: str = Field(..., description="密码", min_length=6, max_length=100)
    phone: str = Field(..., description="手机号", pattern=r"^1[3-9]\d{9}$")


class ThirdPartyLoginReqSchema(BaseModel):
    """第三方登录请求基类"""

    platform: str = Field(..., description="第三方平台名称", examples=["wechat", "alipay"])
    code: str = Field(..., description="授权码", min_length=1, max_length=256)


class WeChatLoginReqSchema(BaseModel):
    """微信登录请求"""

    code: str = Field(..., description="微信授权码", min_length=1, max_length=256)


class AlipayLoginReqSchema(BaseModel):
    """支付宝登录请求"""

    auth_code: str = Field(..., description="支付宝授权码", min_length=1, max_length=256)


class ThirdPartyBindPhoneReqSchema(BaseModel):
    """第三方账号绑定手机号请求"""

    platform: str = Field(..., description="第三方平台名称", examples=["wechat", "alipay"])
    phone: str = Field(..., description="手机号", pattern=r"^1[3-9]\d{9}$")
    sms_code: str = Field(..., description="短信验证码", min_length=4, max_length=8)


class UserLoginRespSchema(BaseModel):
    """用户登录响应"""

    user: "UserDetailSchema"
    token: str = Field(..., description="访问令牌")


class UserListReqSchema(BaseModel):
    name: str = Field(min_length=1, max_length=20)


class UserDetailSchema(BaseModel):
    id: int
    name: str
    phone: str


class UserListResponseSchema(BaseListResponse):
    items: list[UserDetailSchema]
