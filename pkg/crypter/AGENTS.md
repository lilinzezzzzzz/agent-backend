# AGENTS.md

`pkg/crypter/` 的密文被 `internal/config.py` 中的 `ENC(...)` 配置链使用。

- 新算法通过 `BaseCipher`、`EncryptionAlgorithm` 和现有 factory 注册，不建立旁路入口。
- 明文、密钥和中间缓冲不得进入日志或异常；错误使用模块内可区分类型。
- 密文格式、密钥派生和算法模式是跨部署 artifact。变更时保留旧密文解密能力或提供明确迁移方案。
- 算法变化至少覆盖 round-trip、错误密钥、损坏密文、边界长度和 `ENC(...)` 配置解密。
