# AGENTS.md

适用于 `internal/services/`。

## 层职责

Service 层承载业务用例、领域规则、跨 DAO 编排、缓存一致性和外部能力调用。Controller 应调用 Service，而不是直接访问 DAO 或基础设施。

## 编码约定

- 对外提供清晰的业务方法，方法名以业务动作表达，不暴露 SQL 或缓存实现细节。
- 通过 `internal/dao/` 访问数据库或缓存。
- 多个 DAO 协作时，在 Service 中统一处理事务边界、幂等性和错误转换。
- 输入输出优先使用明确类型；Service 对外返回业务数据 DTO，优先使用 dataclass、namedtuple
  或 TypedDict，不直接返回响应信封。
- Service 对外返回的业务 DTO 统一定义在 `internal/services/dto/<domain>.py`，
  按业务域拆文件；只在单个 Service 方法内部使用的临时私有数据结构可以留在当前文件。
- 业务 DTO 不依赖 `internal/schemas/`，不提供 `to_schema()` / `to_response()`；DTO 到
  API response schema 的转换由对应 response schema 的 `from_dto()` 类方法负责。
- 业务异常统一继承项目基类 `AppException`（错误码稳定性规则见全局 `~/.qoder/AGENTS.md`）。
- 写入后需要立即读取一致数据时，使用 DAO 提供的主库查询能力。
- 通用分层命名规则见根目录 `AGENTS.md`，Service 层不重复定义。

## 代码最小正确形态

一个合格 Service 的最小形态：

```python
from internal.core import AppException, errors
from internal.dao.user import UserDao, new_user_dao
from internal.services.dto.user import UserDetailDTO


class UserService:
    def __init__(self, *, user_dao: UserDao):
        self._user_dao = user_dao

    async def get_user_detail(self, *, user_id: int) -> UserDetailDTO:
        user = await self._user_dao.get_by_id(user_id)
        if user is None:
            raise AppException(errors.NotFound, message="用户不存在")

        return UserDetailDTO(id=user.id, name=user.name, phone=user.phone)


# 全局单例（懒加载）
_user_service: UserService | None = None


def new_user_service() -> UserService:
    """依赖注入：获取 UserService 单例"""
    global _user_service
    if _user_service is None:
        _user_service = UserService(user_dao=new_user_dao())
    return _user_service
```

最低要求：

- 构造函数显式注入 DAO 或外部依赖，便于测试替换。
- 方法参数使用 keyword-only，返回值类型明确。
- 提供 `new_xxx_service()` 工厂函数作为 FastAPI 依赖注入入口，采用模块级 `_xxx_service` 懒加载单例，避免每次请求重复构造无状态 Service。
- 业务存在性、权限、状态流转等规则在 Service 内判断。
- DAO 异常不在这里静默吞掉；业务不可满足时转换为稳定 `AppException`。
- 返回 Controller 需要的业务 DTO，不把多余 ORM 细节暴露给 API 层。
- 不在 Service 方法里调用 `success_response`、`success_list_response`、`error_response`
  或构造 `BaseResponse` / `BaseListResponse`。

## 数据安全

- 不要把数据库操作放在循环中。批量场景先批量查出依赖数据，再在内存中组织业务逻辑，最后批量写入。
- 涉及账号、认证、第三方登录、缓存 key、任务提交时，确认幂等性、过期时间和失败补偿。
- 日志保留业务定位信息，但不能泄漏 secret、密码、完整 token 或外部授权 code。

## 验证重点

- Service 单元测试优先覆盖 happy path、业务错误、边界值和幂等行为。
- 涉及 DAO/Redis/Celery 的跨边界行为，再补少量集成测试。
