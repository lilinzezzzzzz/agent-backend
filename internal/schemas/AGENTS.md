# AGENTS.md

适用于 `internal/schemas/`。

## 层职责

本层定义 API 请求、响应和输入输出校验模型。它是外部 API contract 的主要承载层。

## 编码约定

- 使用 Pydantic v2，不引入 v1 兼容写法。
- 请求和响应模型分开定义，避免把内部 ORM model 直接暴露给外部。
- 字段类型要具体，避免 loose `dict`、`list` 或 `Any`。
- 需要复用字段时使用小的基础 schema，但不要为了少量字段创建过深继承层级。
- 对外字段命名、必填性、默认值和枚举值属于兼容性边界，修改前要评估调用方影响。
- Service 层业务 DTO 可以通过 `to_schema()` 转换为本层 response schema；schema
  自身不得反向依赖 Service、DAO、ORM 或缓存。
- 通用分层命名规则见根目录 `AGENTS.md`，Schema 层不重复定义。

## 代码最小正确形态

一个合格 Schema 的最小形态：

```python
from pydantic import BaseModel, Field


class UserCreateReqSchema(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    phone: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=8, max_length=128)


class UserDetailSchema(BaseModel):
    id: int
    name: str
    phone: str
```

最低要求：

- 请求模型和响应模型分开，命名体现方向，例如 `ReqSchema` / `RespSchema` / `DetailSchema`。
- 字段使用具体类型和必要 `Field` 约束。
- 响应模型不包含密码、token secret、内部缓存 key 或数据库内部字段。
- schema 只做传输层格式校验，不访问数据库，不判断业务存在性。
- 兼容性字段不要随意改名、改必填或改默认值。
- response schema 只表达 API contract，不承担业务 DTO 的构造、查询或缓存逻辑。

## 校验规则

- 传输层校验放在 schema，业务规则放在 Service。
- 密码、token、secret 等敏感字段不要出现在响应模型中。
- 时间、金额、ID、手机号、第三方登录 code 等字段要使用明确类型和必要约束。

## 验证重点

- API 测试覆盖请求校验失败、缺字段、非法字段和值边界。
- 修改响应模型时同步检查 Controller `response_model` 和 README/docs 中的 API 示例。
