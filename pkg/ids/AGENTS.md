# AGENTS.md

适用于 `pkg/ids/`。

## 模块职责

本模块提供 UUIDv7 字符串/整数 ID、Trace ID 规范化和 Snowflake ID 生成。

## 修改约束

- `uuid6_unique_str_id()` 的 32 位小写 UUIDv7 hex 格式被会话、run、trace 和幂等记录依赖，按持久化 contract 处理。
- `normalize_uuid7_trace_id()` 只接受合法、非全零 UUIDv7；不要放宽为任意 32 位字符串。
- 修改 UUID 整数截取位数、排序性或 Snowflake bit/node 规则前检查数据库字段、索引、跨进程唯一性和
  前端精度。
- Node ID 选择必须在多 Worker / 多主机环境避免碰撞；不要以随机或不稳定值掩盖部署配置问题。
- ID 不是 secret，不用于认证、授权或不可猜测 token；安全 token 使用专用 CSPRNG。
- 不在生成失败日志中泄露主机敏感信息。

## 验证重点

- 覆盖格式、版本、非零、时间排序、批量唯一性、非法 Trace ID 和多 node 场景。
- 修改 Trace ID 时同时运行 middleware、request context 和 tracing 测试。
