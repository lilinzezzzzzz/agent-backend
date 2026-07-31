# AGENTS.md

`configs/` 只保存各 `APP_ENV` 的非敏感运行参数；配置来源和密钥边界遵循根规则及
`internal/config.py`。

- 新增必填配置时同步 `Settings`、所有适用环境文件、provider 和配置测试；跨环境使用同名 key，关闭能力用
  明确值表达，不用缺失字段表达。
- 密码、token、API key、私钥和真实生产凭据不得进入本目录；密钥示例只放 `.secrets.example`。
- 不绕过 `Settings.settings_customise_sources()`，不新增 `APP_ENV` 以外的 shell 环境变量兜底。
- `CONFIG_ECHO_REVEAL_SECRETS` 默认保持关闭，配置 echo 和异常信息必须脱敏。
