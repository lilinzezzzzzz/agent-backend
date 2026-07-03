# AGENTS.md

适用于 `internal/schemas/`。

## 层职责

本层定义 API 请求/响应模型、稳定 wire 枚举、传输层校验模型，以及 Service 直接返回给 Router
并用于响应映射的轻量 DTO。它是外部 API contract 和 Router 响应映射数据结构的主要承载层。

## 编码约定

- 使用 Pydantic v2，不引入 v1 兼容写法。
- 请求和响应模型分开定义，避免把内部 ORM model 直接暴露给外部。
- 稳定 wire 枚举、请求/响应 schema 和轻量 DTO 按业务域放在同一个 `<domain>.py` 文件中。
- 字段类型要具体，避免 loose `dict`、`list` 或 `Any`。
- 需要复用字段时使用小的基础 schema，但不要为了少量字段创建过深继承层级。
- 对外字段命名、必填性、默认值和枚举值属于兼容性边界，修改前要评估调用方影响。
- Service 层业务 DTO 优先使用 `dataclass(frozen=True, slots=True)`，命名使用 `*DTO` 后缀，
  可以通过 `to_schema()` 转换为本层 response schema；DTO 只能做数据承载和纯字段映射，
  不访问数据库、Redis、上下文、配置或外部服务。
- schema 和 DTO 自身不得反向依赖 Service、DAO、ORM 或缓存。
- 通用分层命名规则见根目录 `AGENTS.md`，Schema 层不重复定义。

## 代码最小正确形态

一个合格 Schema 的最小形态：

```python
from dataclasses import dataclass

from pydantic import BaseModel, Field


class UserCreateReqSchema(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    phone: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=8, max_length=128)


class UserDetailSchema(BaseModel):
    id: int
    name: str
    phone: str


@dataclass(frozen=True, slots=True)
class UserDetailDTO:
    id: int
    name: str
    phone: str

    def to_schema(self) -> UserDetailSchema:
        return UserDetailSchema(id=self.id, name=self.name, phone=self.phone)
```

最低要求：

- 请求模型和响应模型分开，命名体现方向，例如 `ReqSchema` / `RespSchema` / `DetailSchema`。
- Service 返回给 Router 的轻量 DTO 命名使用 `*DTO`，只承载数据和响应映射逻辑。
- 字段使用具体类型和必要 `Field` 约束。
- 响应模型不包含密码、token secret、内部缓存 key 或数据库内部字段。
- schema 只做传输层格式校验，不访问数据库，不判断业务存在性。
- 兼容性字段不要随意改名、改必填或改默认值。
- response schema 只表达 API contract；DTO 不承担查询、缓存或业务规则判断逻辑。

## 校验规则

- 传输层校验放在 schema，业务规则放在 Service。
- 密码、token、secret 等敏感字段不要出现在响应模型中。
- 时间、金额、ID、手机号、第三方登录 code 等字段要使用明确类型和必要约束。

## 验证重点

- API 测试覆盖请求校验失败、缺字段、非法字段和值边界。
- 修改响应模型或 DTO 字段时同步检查 Service 返回值、Controller `response_model` / 响应组装、
  README/docs 中的 API 示例。
