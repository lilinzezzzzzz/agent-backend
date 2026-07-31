# AGENTS.md

`pkg/security/` 提供密码哈希、JWT 和 HMAC 签名原语，不承担用户权限或业务认证流程。

- secret、算法、过期时间和容忍窗口由调用方注入；密码使用慢哈希并 offload 阻塞计算。
- JWT 只接受显式 allowlist 算法，保持 claims、过期和 Bearer 解析语义，不允许 `none`。
- 签名生成与验证使用相同 canonicalization、字段、时间单位和 tolerance，并用 constant-time 比较。
- 不降低安全参数或新增弱算法默认值；移除历史算法前提供调用方迁移窗口。
- 日志和错误不得输出密码、hash 细节、token、secret、签名或完整待签名敏感数据。
