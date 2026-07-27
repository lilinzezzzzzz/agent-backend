# AGENTS.md

适用于 `pkg/security/`。认证接入规则见 `internal/middlewares/AGENTS.md` 与 `internal/services/auth.py`。

## 模块职责

本模块提供密码哈希、JWT 创建/验证和 HMAC 请求签名原语，不负责用户权限、token 存储或业务认证流程。

## 修改约束

- 不依赖 `internal/`；secret、算法、过期时间和容忍窗口通过构造函数注入。
- 密码哈希保持慢哈希并 offload 阻塞计算；不得降低 rounds、记录明文密码或暴露 hash 诊断细节。
- JWT algorithm allowlist、Bearer 解析、claims 和过期语义属于兼容与安全 contract；不允许 `none` 或
  未显式允许算法。
- 签名 canonicalization、字段集合、时间戳单位和 tolerance 必须在生成端与验证端一致；比较使用
  constant-time API。
- 不新增弱算法作为默认值。移除历史算法前先确认调用方和迁移窗口。
- 日志不得输出 token、secret、签名、nonce 全值或完整待签名敏感数据。
- ID、trace_id 和普通 nonce 不能替代认证 secret；重放防护需要时间窗之外的去重时由业务层实现。

## 验证重点

- 运行 `tests/security/` 与认证 middleware/API 测试。
- 覆盖正确/错误密码、异常 hash、JWT 过期/篡改/缺 claim、签名篡改、时间窗边界和敏感日志。
