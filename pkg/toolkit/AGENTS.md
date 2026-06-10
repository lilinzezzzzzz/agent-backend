# AGENTS.md

适用于 `pkg/toolkit/`。父级约束见 `pkg/AGENTS.md`。

## 模块职责

细粒度通用工具集合。每个 `.py` 文件一个明确职责：

- 基础集合/字符串/数值：`dict.py`、`list.py`、`string.py`、`float.py`
- 序列化/时间：`json.py`、`timer.py`
- 薄协议工具：`grpc.py`、`middleware.py`
- 其他：`exc.py`

外部依赖 client、生命周期管理、请求上下文、响应 contract、认证安全、ID 生成、智能类型和异步文件工具已拆到 `pkg/redis`、`pkg/http_client`、`pkg/celery_queue`、`pkg/scheduler`、`pkg/api_response`、`pkg/request_context`、`pkg/security`、`pkg/ids`、`pkg/lazy_proxy`、`pkg/smart_types`、`pkg/files`、`pkg/config_loader`。

## 编码约定

- **单一职责**：新增工具优先放到已有文件；只有跨领域或体量超过 200 行时才新开文件。严禁出现 `utils.py` / `helpers.py` 这种聚合文件。
- **零业务依赖**：`toolkit` 不得 import `internal/`；被 `internal/` 与其他 `pkg/*` 共同依赖。
- **向外类型清晰**：公共函数、协议、类必须有类型注解。内部数据结构优先 `dataclass` 或 `TypedDict`，避免 `dict[str, Any]`。
- **无副作用 import**：模块顶层禁止执行网络、文件、数据库、日志 IO；配置需要的值通过函数参数或构造注入。
- **异步优先**：涉及 I/O 的工具提供 `async` 版本；同步阻塞版本只作为显式对照存在。
- **不引入新的第三方依赖**：新增依赖需在 `pyproject.toml` 变更并说明；能用现有依赖实现的不新增。

## 兼容性要求

- 任何 `pkg/toolkit/*` 的公开符号（函数、类、类型别名）都可能被多处 import；重命名 / 删除 / 改签名前必须全仓检索。
- 不要在 `pkg/toolkit` 新增有连接生命周期、外部协议 contract、认证安全、请求上下文、任务队列或调度器职责的模块；这些能力应放到对应 `pkg/*` 主题包。

## 验证重点

- 单个工具改动优先运行 `tests/toolkit/` 下对应测试。
- 改 Redis / HTTP 客户端时，同时覆盖成功、超时、重试、异常四条路径。
