# AGENTS.md

适用于 `internal/controllers/` 及其子目录。

## 层职责

本层是 HTTP API 入口，负责路由声明、请求接收、依赖注入、认证上下文读取和响应组装。

- `api/`：`/v1` 业务 API。
- `public/`：`/v1/public` 公共 API。
- `internal/`：`/v1/internal` 内部 API。

## 编码约定

- Controller 保持薄层，不承载业务规则、复杂查询、缓存编排或外部服务编排。
- 请求体、响应体使用 `internal/schemas/` 中的 Pydantic v2 model。
- `response_model` 统一使用 `BaseResponse[T]` / `BaseListResponse[T]` 声明响应信封（用于 OpenAPI schema 和响应校验）。
- 实际返回值使用 `pkg.api_response` 中的 payload 工厂函数：成功调用 `success_response(data=...)`，分页调用 `success_list_response(data=..., page=..., limit=..., total=...)`。这些函数返回可被 FastAPI `response_model` 校验和序列化的响应载荷，不要在正常 Controller 中直接返回 HTTP `Response`。
- `error_response(error, message=..., lang=...)` 仅用于中间件、异常处理器等必须直接构造 HTTP `Response` 的路径；业务错误在 Controller / Service 中抛 `internal.core.AppException`。
- 当 Service 返回 dataclass / DTO 且该对象提供 `to_schema()` 时，Controller 负责调用
  `dto.to_schema()` 得到对应 response schema，再传给响应工厂函数。
- Service DTO 定义位置遵循根目录规则，默认来自 `internal/services/dto/<domain>.py`；
  Controller 不在本层定义 DTO。
- 业务逻辑调用 `internal/services/`，不要直接操作 ORM session。
- 需要读当前用户时使用上下文工具，例如 `pkg.request_context.get_user_id()`，不要重新解析 token。
- 业务错误使用 `internal.core.AppException` 和 `internal.core.errors`。
- 路由 prefix、tags、summary 要与现有风格一致。
- 新增公开接口时确认认证中间件白名单或路由前缀是否符合预期。
- 每个 Router 方法必须编写详细的 docstring，按固定结构组织，便于生成 OpenAPI 描述与人工 review：
  - **业务摘要**：一句话描述该接口完成的业务动作（动词开头，避免技术实现细节）。
  - **权限边界**：说明认证要求（匿名 / 普通 token / 内部签名）、所需角色或资源归属校验（如仅本人、仅管理员、仅租户成员）。
  - **业务边界**：说明该接口的副作用范围、幂等性、对外部系统（DB、Redis、Celery、第三方）的影响，以及明确**不**承担的职责。
  - **Args**：列出路径参数、查询参数、请求体字段与依赖项，逐项说明含义、取值范围与必填性。
  - **Returns**：说明成功响应的数据结构（引用对应 schema）以及主要错误码（对应 `internal.core.errors`）。
- 业务逻辑、参数、返回结构、权限规则、错误码或副作用发生变动时，必须在同一次改动中同步更新对应 Router 的 `summary` 与 docstring（业务摘要 / 权限边界 / 业务边界 / Args / Returns），保持代码与 OpenAPI 描述一致，禁止让 docstring 滞后于实现。
- 通用分层命名规则见根目录 `AGENTS.md`，Controller 层不重复定义。

## 代码最小正确形态

一个合格 Controller 的最小形态：

```python
from typing import Annotated

from fastapi import APIRouter, Depends

from internal.schemas import BaseResponse
from internal.schemas.user import UserDetailSchema
from internal.services.user import UserService, new_user_service
from pkg.api_response import ResponsePayload, success_response

router = APIRouter(prefix="/user", tags=["api user"])

UserServiceDep = Annotated[UserService, Depends(new_user_service)]


@router.get(
    "/{user_id}",
    response_model=BaseResponse[UserDetailSchema],
    summary="获取用户详情",
)
async def get_user(
    user_id: int,
    user_service: UserServiceDep,
) -> ResponsePayload:
    """获取指定用户的详情信息。

    业务摘要:
        根据 user_id 查询单个用户的基础信息与展示字段，用于个人主页等场景。

    权限边界:
        需要有效的用户 token（`/v1` 前缀默认认证）；仅允许查询本人或公开可见的用户资料，
        非本人且非公开字段由 Service 层过滤。

    业务边界:
        只读接口，无写副作用；仅读取用户主表与必要缓存，不触发第三方同步，
        不承担鉴权决策以外的权限下发。

    Args:
        user_id: 目标用户的唯一 ID，路径参数，必填，正整数。
        user_service: 通过依赖注入获取的 `UserService` 实例。

    Returns:
        `BaseResponse[UserDetailSchema]`：成功时返回用户详情；
        用户不存在返回 `errors.USER_NOT_FOUND`，无权限返回 `errors.PERMISSION_DENIED`。
    """
    user = await user_service.get_user_detail(user_id=user_id)
    return success_response(data=user.to_schema())
```

最低要求：

- 只声明路由、参数、依赖和 response model。
- `response_model` 统一使用 `BaseResponse[T]` 或 `BaseListResponse[T]` 包装业务 schema，保持响应信封一致。
- 参数名和 schema 字段名与 API contract 一致。
- 调用一个明确的 Service 方法完成用例，Service 返回业务数据；若返回 DTO，Controller 负责转换为
  response schema，再通过 `success_response` / `success_list_response` 包装正常响应载荷。
- 正常 Controller 返回 payload，由 FastAPI `response_model` 执行校验、字段过滤和 JSON 序列化；不要手动构造 HTTP `Response`。
- 需要当前用户时只读取上下文，不自行校验 token。
- 必须提供完整 docstring，至少包含 **业务摘要 / 权限边界 / 业务边界 / Args / Returns** 五个小节，缺一不可。

## 禁止事项

- 不要在 handler 中写 SQLAlchemy 查询、Redis 操作或循环内数据库访问。
- 不要在日志中打印完整 token、密码、密钥、连接串或第三方登录凭证。
- 不要绕过统一错误和认证中间件返回临时错误结构。

## 验证重点

- API contract：请求字段、响应字段、状态码、错误码。
- 认证行为：匿名、普通 token、内部签名路径是否符合路由前缀。
- 回归测试优先放在 `tests/api/` 或相关 service 单元测试中。
