# AGENTS.md

适用于 `configs/`。配置加载 contract 以 `internal/config.py` 和根目录 `AGENTS.md` 为准。

## 目录职责

本目录只保存各 `APP_ENV` 的非敏感应用运行参数。根目录 `.env` 只选择 `APP_ENV`，根目录 `.secrets`
只保存 `internal.config.SECRET_FILE_KEYS` 允许的凭据。

## 修改约束

- 新增必填非敏感配置时，同步更新 `Settings`、`.env.local`、`.env.dev`、`.env.test`、`.env.prod` 和相关测试。
- 不在本目录写入密码、token、API key、私钥或真实生产凭据；密钥示例只维护在 `.secrets.example`。
- 除 `APP_ENV` 外，不增加 shell 环境变量兜底；配置来源顺序不得绕过 `Settings.settings_customise_sources()`。
- 保持配置 key 大写且跨环境同名。环境差异只体现在值，不通过漏字段表达“关闭”。
- 修改日志、OTLP、数据库、Redis、Celery、LLM、Embedding、Milvus 或 Endpoint Guard 参数时，同步检查对应
  provider、生命周期、部署文档和边界校验。
- `CONFIG_ECHO_REVEAL_SECRETS` 默认保持关闭；不得通过配置 echo 或异常消息泄露敏感值。

## 验证重点

- 优先运行 `uv run pytest tests/config/test_config.py -q`。
- 同步检查所有环境文件的必填 key、secret/non-secret 分离和 `APP_ENV` 唯一来源。
