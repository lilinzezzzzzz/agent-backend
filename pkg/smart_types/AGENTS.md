# AGENTS.md

`pkg/smart_types/` 的 Python 类型、输入解析、JSON 输出和 OpenAPI schema 必须保持一致。

- `SmartInt` 超出 JavaScript safe integer 范围时序列化为字符串；`IntStr` 保持大 ID 的字符串输出。
- `SmartDecimal` 内部使用 Decimal，金额路径不使用二进制浮点作为权威值。
- `SmartDatetime` 规范为 naive UTC 并输出 UTC ISO 8601；时区或精度变化按 wire contract 处理。
- bool 不作为 int 接受，非法值明确报 validation error，不静默截断或取整。
- 变更覆盖 JS 边界、Decimal 精度、时区/DST、非法输入和生成的 JSON Schema。
