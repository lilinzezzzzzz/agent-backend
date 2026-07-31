# AGENTS.md

`pkg/ids/` 提供 UUIDv7、Trace ID 规范化和 Snowflake ID。

- `uuid6_unique_str_id()` 的 32 位小写 UUIDv7 hex 格式是持久化 contract。
- `normalize_uuid7_trace_id()` 只接受合法、非全零 UUIDv7；不能放宽成任意 32 位字符串。
- UUID 截取位数、排序性和 Snowflake bit/node 规则变化时检查字段宽度、索引、前端精度及跨进程唯一性。
- 多 Worker / 多主机的 node ID 必须稳定且无碰撞；普通 ID 不得当作认证 secret 使用。
