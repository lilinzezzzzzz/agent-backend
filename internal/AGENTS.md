# AGENTS.md

适用于 `internal/` 及其子目录；子目录存在更具体 `AGENTS.md` 时叠加遵循最近规则。

## 应用边界

`internal/` 承载应用装配与业务实现，可以依赖 `pkg/`；`pkg/` 不得反向依赖 `internal/`。跨层依赖方向保持为
Controller → Service / Agent Service → DAO / Cache / Infra adapter → `pkg` 基础能力。

根目录文件职责：

- `app.py`：FastAPI app、router / exception / middleware 注册与进程生命周期编排。
- `config.py`：`Settings` contract、配置来源隔离、校验、脱敏输出和延迟单例。

## 修改约束

- 不在模块 import 时主动连接数据库、Redis、LLM、Milvus、Collector 或启动后台任务。
- `internal.app.lifespan` 统一拥有 logger、tracing、DB、Redis、签名、ID generator 和后台任务的初始化/关闭；
  调整顺序时检查依赖关系、部分启动失败和重复清理。
- Router、middleware 和 exception 注册保持集中，新增入口不得绕过 `/v1`、`/v1/public`、`/v1/internal`
  的认证 contract。
- `Settings` 新字段必须明确属于非敏感环境配置还是 `.secrets`；同步所有环境文件、示例、provider 和测试。
- 保持配置 `extra="forbid"`、必填字段校验和 secret/non-secret 分离，不增加未声明的 shell 环境兜底。
- 配置日志默认脱敏；异常和启动日志不得输出 SecretStr 明文或完整连接 URI。
- 子层复用现有 schema、Service、DAO、Cache 和 Infra provider，不在 `app.py` / `config.py` 建立业务旁路。

## 验证重点

- 配置变化优先运行 `uv run pytest tests/test_config.py -q`。
- 生命周期或 app 装配变化至少覆盖 FastAPI startup / shutdown、middleware 顺序和 provider 清理路径。
- 修改子目录前继续读取最近的 `AGENTS.md`。
