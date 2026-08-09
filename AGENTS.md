# AGENTS.md

本文件只记录无法可靠地从代码、配置和测试中推断的项目级约束。目录存在更具体的 `AGENTS.md` 时，
仅叠加其中的局部规则。

## 项目边界

- 本仓库是面向 Agent、RAG 与 AI 平台场景的 FastAPI demo 和后端脚手架；运行方式与依赖版本以
  `README.md`、`pyproject.toml` 和现有 entrypoint 为准。
- `plans/pending/` 保存未完成计划，`plans/completed/` 只保存实现和验证均有证据的历史计划。
- 计划状态、checklist 和验证结论必须与仓库事实一致；废弃计划标记替代方案，已完成计划不回写成当前设计。
- `internal/` 承载应用装配和业务实现，可以依赖 `pkg/`；`pkg/` 是可复用基础包，不得反向依赖
  `internal/`。
- 修改现有能力时优先沿用相邻代码的 Controller / Service / DAO / Cache / Schema / Infra 边界，
  不为同一职责新增平行抽象。

## 稳定 Contract

- 路由前缀决定认证边界：`/v1` 默认 token 认证，`/v1/public` 默认匿名，`/v1/internal` 默认内部签名。
- HTTP 响应使用现有 `BaseResponse[T]` / `BaseListResponse[T]` 信封和 `pkg.api_response` 工厂；
  `internal.core.errors` 的错误码语义保持稳定。
- 数据库写入通过 Service / DAO 或已有数据库 helper 完成，不在 Controller 中直接操作 session。
- Service 返回给 Router 的轻量 DTO 使用 `*DTO` 命名，放在 `internal/schemas/<domain>.py`；请求和响应
  schema 分别使用 `*ReqSchema`、`*RespSchema` 或现有 detail/item 命名。
- 缓存 key/TTL、Celery task name/queue、向量 collection schema、公开 API 字段和持久化格式均按兼容性
  contract 处理；修改时同步受影响的实现、测试和必要文档。

## 配置与安全

- `internal/config.py` 是配置加载入口。根目录 `.env` 只选择 `APP_ENV`，应用读取
  `configs/.env.{APP_ENV}` 和根目录 `.secrets`；除 `APP_ENV` 外不增加 shell 环境变量兜底。
- 非敏感运行参数必须在环境配置中声明，凭据只能放在 `.secrets` 允许的字段中；不得提交或记录真实
  secret、token、密码、私钥、数据库连接串或生产配置。
- 根 `Dockerfile`、`compose.yaml`、`.env.compose.example` 与 `deploy/` 共同构成容器部署 contract；
  保持 API 非 root、配置和密钥只读挂载、logrotate 单副本及最小权限边界。

## 数据与验证

- 当前仓库以全新建库基线 DDL 为主。只有任务明确涉及已存在环境时才新增迁移，并评估历史数据、锁表、
  部署顺序、兼容窗口和回滚；部署状态发生变化时必须重新审视本规则。
- Python 版本、Ruff 和测试配置以 `pyproject.toml` 为准。运行能覆盖改动行为的最小验证，再按共享 contract
  的影响范围扩大；不得声称未实际运行的检查通过。
- 默认不假设数据库、Redis、Celery Worker、Milvus 或外部模型可用；依赖这些服务的测试标记为
  `integration`，并明确前置条件。
