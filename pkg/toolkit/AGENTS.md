# AGENTS.md

适用于 `pkg/toolkit/`。父级约束见 `pkg/AGENTS.md`。

## 模块职责

细粒度通用工具集合。每个 `.py` 文件一个明确职责：

- 基础类型/字典：`types.py`、`dict.py`、`list.py`、`string.py`、`float.py`
- 序列化/IO：`json.py`、`file.py`、`response.py`
- 客户端：`http_cli.py`、`redis_client.py`、`grpc.py`
- 时间/任务：`timer.py`、`celery.py`、`apscheduler.py`
- 上下文/签名/认证：`context.py`、`signature.py`、`jwt.py`、`hasher.py`
- 其他：`config_loader.py`、`exc.py`、`inter.py`、`middleware.py`

## 编码约定

- **单一职责**：新增工具优先放到已有文件；只有跨领域或体量超过 200 行时才新开文件。严禁出现 `utils.py` / `helpers.py` 这种聚合文件。
- **零业务依赖**：`toolkit` 不得 import `internal/`；被 `internal/` 与其他 `pkg/*` 共同依赖。
- **向外类型清晰**：公共函数、协议、类必须有类型注解。内部数据结构优先 `dataclass` 或 `TypedDict`，避免 `dict[str, Any]`。
- **无副作用 import**：模块顶层禁止执行网络、文件、数据库、日志 IO；配置需要的值通过函数参数或构造注入。
- **异步优先**：涉及 I/O 的工具提供 `async` 版本；同步阻塞版本只作为显式对照存在。
- **不引入新的第三方依赖**：新增依赖需在 `pyproject.toml` 变更并说明；能用现有依赖实现的不新增。

## 兼容性要求

- 任何 `pkg/toolkit/*` 的公开符号（函数、类、类型别名）都可能被多处 import；重命名 / 删除 / 改签名前必须全仓检索。
- `redis_client.RedisClient`、`http_cli`、`response.BaseResponse` / `AppError`、`context.get_user_id`、`jwt`、`signature` 属于高影响面 API，调整按 `BREAKING CHANGE` 处理。

## 验证重点

- 单个工具改动优先运行 `tests/toolkit/` 下对应测试。
- 改 `http_cli` / `redis_client` 时，同时覆盖成功、超时、重试、异常四条路径。
