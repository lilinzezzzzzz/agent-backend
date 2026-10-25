# AGENTS.md

`pkg/ids/` 提供 UUIDv7 生成。

- `uuid7_unique_id()` 返回完整 128 位 UUIDv7，作为数据库主键 contract。
- `uuid7_unique_str_id()` 的 32 位小写 UUIDv7 hex 格式是持久化 contract。
- Trace ID 由 W3C Trace Context 和 FastAPI 原生 telemetry 管理，不在本模块规范化。
- UUID 位数、排序性或持久化格式变化时检查字段类型、索引及跨进程唯一性。
- 普通 ID 不得当作认证 secret 使用。
