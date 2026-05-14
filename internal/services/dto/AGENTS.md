# AGENTS.md

适用于 `internal/services/dto/`。

## 层职责

本目录定义 Service 对外返回给 Controller 的业务 DTO。DTO 属于 Service 层边界，
用于承载业务用例输出，不等同于 API schema，也不等同于 ORM model。

## 编码约定

- 按业务域拆文件，例如 `auth.py`、`order.py`、`knowledge_base.py`。
- DTO 优先使用 `dataclass(frozen=True, slots=True)`，命名使用 `*DTO` 后缀。
- DTO 只做数据承载和纯字段映射；不得访问数据库、Redis、上下文、配置或外部服务。
- DTO 可以提供无副作用的 `to_schema()` 方法，转换为 `internal/schemas/` 中的
  API response schema。
- DTO 不构造 `BaseResponse` / `BaseListResponse`，不调用 `success_response`、
  `success_list_response` 或 `error_response`。

## 验证重点

- Controller 测试优先覆盖 DTO 到 response schema 的字段映射。
- 修改 DTO 字段时同步检查对应 Service 返回值、Controller 响应组装和 response schema。
