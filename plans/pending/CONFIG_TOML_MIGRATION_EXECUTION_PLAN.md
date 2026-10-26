# 配置目录迁移到嵌套 TOML 与配置模型实施计划

状态：已实施，待部署配置验证。目标是把 `configs/` 下的非敏感应用配置迁移为嵌套 TOML，并把
`Settings` 重构为分组配置模型；根目录 `.env`（`APP_ENV` / Compose 参数）和 `.secrets`（扁平凭据）
的职责保持不变。

## 1. 目标与范围

- `configs/.env.{APP_ENV}` 替换为 `configs/app.{APP_ENV}.toml`。
- TOML 使用嵌套 table，例如 `[log]`、`[otel]`、`[db]`、`[db.read_replica]`、`[celery]`。
- `Settings` 使用嵌套 `BaseModel` 分组，代码通过 `settings.log.format`、`settings.db.host`、
  `settings.celery.reconciler_queue` 访问。
- `.secrets` 文件继续使用扁平 dotenv key，加载到独立的 `SecretsSettings`，代码通过
  `settings.secrets.db_password` 等访问。
- 不保留 flat 配置兼容入口；同步所有调用点、测试和文档。
- 根目录 `.env` 只保留 `APP_ENV` 和 Compose 参数；`.secrets` 不与应用 TOML 混合。

不在本次范围：修改 `APP_ENV` 选择机制、把凭据改成 TOML、为旧 flat 字段提供属性别名。

## 2. 关键决策

### 2.1 嵌套模型与 TOML

- 根级字段：`app_env`、`debug`。
- 分组：`log`、`otel`、`auth`、`cors`、`wechat`、`llm`、`embedding`、`milvus`、`agent`、
  `rag`、`db`、`redis`、`celery`、`endpoint_guard`、`config_echo`、`secrets`。
- `llm` 下再分 `mimo` / `deepseek`；`db` 下使用可选 `read_replica`。
- `SecretsSettings` 字段使用小写 Python 名，通过 `validation_alias` 接收 `.secrets` 的大写 key。
- 所有配置模型使用 `extra="forbid"`；跨分组校验留在 `Settings`，分组内部校验放在对应模型。

### 2.2 配置来源

- `Settings.settings_customise_sources()` 返回 `init_settings` + TOML source。
- `load_config()` 先验证 `.secrets` 和 TOML 文件，再把 `SecretsSettings` 注入 `Settings`。
- TOML 中出现 `app_env`、`APP_ENV`、`secrets` 或 `SECRET_FILE_KEYS` 时启动失败。
- 配置回显递归遍历嵌套模型，`secrets.*` 默认脱敏；`config_echo.reveal_secrets` 控制临时显示。

### 2.3 部署边界

- `compose.yaml` 只读挂载 `configs/app.${APP_ENV}.toml` 到容器内同名路径。
- 根目录 `.env` 仍然只提供 `APP_ENV` 和 Compose 参数。
- `.secrets` 仍然不包含 `APP_ENV`，不与应用配置混合。

## 3. 文件与模块变更

| 文件/目录 | 变更 |
| --- | --- |
| `internal/config.py` | 嵌套配置模型、`SecretsSettings`、TOML source、递归校验/回显、派生 DB/Redis URL |
| `configs/app.*.toml` | 四个环境嵌套配置，替换四个 `.env` 文件 |
| `configs/.env.*` | 删除 |
| `internal/**/*.py` | 约 136 处 `settings.*` 改为嵌套访问 |
| `tests/**/*.py` | 配置 mock、fake settings 和断言改为嵌套访问 |
| `compose.yaml` | 挂载路径和注释 |
| `.env.compose.example` | 配置路径注释 |
| `README.md` | 配置说明、加载顺序、TOML 示例 |
| `docs/**/*.md` | 配置路径和 TOML 示例改为嵌套格式 |
| `AGENTS.md`、`configs/AGENTS.md` | 嵌套配置模型和格式约束 |
| `tests/config/test_config.py` | 嵌套 TOML writer、模型校验测试、配置回显测试 |

## 4. 实施步骤

- [x] 重构 `Settings` 为嵌套配置模型，新增 `SecretsSettings`。
- [x] 更新 TOML source、`.secrets` 注入、校验和递归配置回显。
- [x] 生成四个嵌套 `configs/app.*.toml`，保留原有注释和环境差异。
- [x] 删除四个 `configs/.env.*`。
- [x] 更新所有生产代码的 `settings.*` 访问。
- [x] 更新测试 mock、fake settings、配置测试和断言。
- [x] 更新 `compose.yaml`、`.env.compose.example`、README、AGENTS 和文档示例。
- [x] 运行配置测试、全量单元测试和 Ruff。
- [ ] 在可用 Docker 环境执行 `docker compose config` 并完成实际部署验证。
- [ ] 完成部署验证后更新本计划状态并移入 `plans/completed/`。

## 5. 验证计划

- `uv run pytest tests/config/test_config.py -q`
- `uv run pytest tests -m "not integration" -q`
- `uv run ruff check .`
- 对四个 `configs/app.*.toml` 执行 `tomllib` 解析，并用 `_load_settings_from_files()` 加载
  `.secrets.example`。
- 检查旧 dotenv 与嵌套 TOML 的 key/值映射一致。
- 检查仓库中不再有指向 `configs/.env.*` 的有效引用；`docs/logging/log_rotation.md` 的历史引用
  仅作为历史说明保留。
- 不运行真实 Collector、数据库、Redis 或 deployment；相关限制记录在验证结论中。

## 6. 风险与处理

- **嵌套模型字段遗漏**：配置测试和全量测试必须覆盖所有分组；TOML 文件用 `extra="forbid"` 失败关闭。
- **`.secrets` 映射错误**：`SecretsSettings` 使用显式 alias；秘密字段不进入 TOML。
- **跨分组校验丢失**：Celery 与 DB、Agent 内部时序、日志输出、OTel exporter 校验必须在对应模型或
  `Settings` 中保留。
- **配置回显泄露**：递归遍历时必须按 `secrets.*` 路径脱敏。
- **文档/调用点遗漏**：全仓搜索 flat `settings.FIELD` 和旧文档字段名。
- **脚手架无兼容负担**：不保留 flat 属性别名；回滚时同时回退配置文件和代码。

## 7. 验证结论

已实施并完成本地单元验证：

- `uv run pytest tests/config/test_config.py -q` → **43 passed**。
- `uv run pytest tests -m "not integration" -q` → **828 passed, 21 deselected**。
- `uv run ruff check .` → 通过。
- 四个嵌套 TOML 均可加载；旧 dotenv 与新 TOML 的 key/值映射逐项一致。
- 真实 `load_config()` 在当前 `.env`/`.secrets` 下成功加载 `app.local.toml`。

未完成：当前环境没有 Docker CLI，未执行 `docker compose config`、真实容器启动、数据库/Redis/Collector
联调。因此计划暂留 `pending/`，待部署配置验证完成后再迁移到 `completed/`。
