# AGENTS.md

适用于 `internal/infra/`。

## 层职责

本层负责数据库、Redis、LLM 等基础设施连接生命周期和 provider。它提供连接能力，不承载业务规则。

- 数据库连接初始化在 `internal/infra/database/`。
- Redis 连接初始化在 `internal/infra/redis/`。
- LLM 客户端 provider 在 `internal/infra/llm/`。

## 编码约定

- 初始化和关闭应由 FastAPI lifespan、Celery worker hook 或测试 fixture 控制。
- 对外暴露 session/provider/client 获取函数，避免业务层自行创建 engine、pool 或 Redis client。
- 连接配置来自 `internal.config.settings`，不要硬编码环境配置或密钥。
- 读写分离、连接池、超时、echo、重试等变化要兼顾 API 服务和 Celery Worker。
- 清理逻辑必须可重复调用，避免测试或进程退出时泄漏连接。

## 风险边界

- 修改连接 URI、pool 参数、事务生命周期或 session scope 可能影响全局请求和任务执行。
- 不要在 infra 层吞掉连接失败；启动失败应明确暴露。
- 不要把业务缓存 key、业务 SQL、Agent prompt、工具逻辑或任务逻辑放进 infra。

## 验证重点

- 单元测试可 mock provider。
- 集成测试需要真实 DB/Redis 时必须显式说明依赖和环境变量。
- 修改生命周期时至少验证应用启动、关闭、测试 fixture 创建和清理路径。
