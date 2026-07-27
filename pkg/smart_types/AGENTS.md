# AGENTS.md

适用于 `pkg/smart_types/`。

## 模块职责

本模块定义 Pydantic v2 输入解析、JSON serializer 和 JSON Schema 一致的智能标量类型。

## 修改约束

- Python 内部类型、可接受输入、JSON 输出和 OpenAPI schema 必须同步变化，不能只改 parser 或
  serializer 一侧。
- `SmartInt` 超出 JavaScript safe integer 范围时继续序列化为字符串，避免前端精度损失。
- `SmartDecimal` 内部使用 Decimal；只有满足明确安全范围时才输出 float，不在金额路径引入二进制浮点
  权威值。
- `SmartDatetime` 将输入规范为 naive UTC 并输出 UTC ISO 8601；改变时区或精度语义属于 wire contract 变更。
- bool 不作为 int 接受；非法字符串和不支持类型明确报 validation error，不静默截断或取整。
- `IntStr` 的字符串输出用于大 ID 兼容，修改前检查 API schema、前端和数据库映射。

## 验证重点

- 运行 `tests/smart_types/` 和 schema 序列化测试。
- 覆盖 JS 边界、Decimal 精度/科学计数、时区与 DST、bool/None、非法字符串和生成的 JSON Schema。
