# AGENTS.md

`configs/` 只保存各 `APP_ENV` 的非敏感运行参数；配置来源和密钥边界遵循根规则及
`internal/config.py`。

- 环境配置文件使用 `app.{APP_ENV}.toml`，跨环境保持同名 table 和字段；关闭能力用明确值表达，不用缺失字段表达。
- 使用嵌套 TOML table，字段名与 `Settings` 的分组模型一致；使用原生布尔、整数和数组，不写 JSON-in-string。
- 新增必填配置时同步 `Settings`、所有适用环境文件、provider 和配置测试。
- 密码、token、API key、私钥和真实生产凭据不得进入本目录；密钥示例只放 `.secrets.example`。
- 不绕过 `Settings.settings_customise_sources()`，不新增 `APP_ENV` 以外的 shell 环境变量兜底。
- `config_echo.reveal_secrets` 默认保持关闭，配置 echo 和异常信息必须脱敏。
