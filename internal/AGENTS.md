# AGENTS.md

`internal/` 承载应用装配和业务实现，可以依赖 `pkg/`，但不得建立 `pkg/ → internal/` 的反向依赖。

## 分层边界

- Controller：声明路由、认证依赖、请求/响应 schema 和响应组装；不直接查询数据库、操作缓存或编排外部服务。
- Service：承载业务规则、权限、幂等、事务以及 DAO / Cache / 外部能力编排；不构造 HTTP 响应信封。
- DAO / Cache：分别封装持久化访问和业务缓存语义，不承担业务状态判断；批量场景使用批量查询和写入，
  避免循环 I/O。
- Schema / DTO：承载传输校验、wire enum 和纯字段映射，不访问数据库、缓存、上下文、配置或外部服务。
- Model：只描述 ORM 映射和持久化字段；字段、索引、约束及默认值与 DDL 保持一致。
- Infra：拥有数据库、Redis、LLM、Celery 和调度器等连接生命周期；业务层不自行创建重复连接池。
- Middleware：处理请求上下文、日志、guard 和异常转换；token 与内部签名认证仍由
  `internal/dependencies/auth.py` 按 Router 边界执行。
- Task：复用 Service / DAO，不复制 Controller 逻辑；按 at-least-once 语义设计并保持 task name 稳定。

## 应用与 API Contract

- `internal.app.lifespan` 统一编排进程资源的初始化和关闭；模块 import 时不得连接外部服务或启动后台任务。
- Router 按 `/v1`、`/v1/public`、`/v1/internal` 认证边界挂载，正常响应使用现有 response model 和
  `pkg.api_response` payload 工厂，业务错误抛 `AppException`。
- 仅在认证、资源归属、副作用、幂等或特殊错误语义无法从签名和 schema 看出时补充 Router docstring；
  不要求固定章节模板。
- Service、DAO 和 Cache 优先沿用现有 `new_*()` factory。只有生命周期与测试隔离允许时才复用模块级实例，
  不把单例作为新增实现的硬性要求。
- 错误码、HTTP 状态映射、公开字段、DTO 映射、缓存 key/TTL 和任务参数属于兼容性边界。

## 数据、配置与生命周期

- `Settings` 字段必须明确属于非敏感环境配置或 `.secrets`，并同步适用环境文件、provider 和测试；
  保持 `extra="forbid"` 及日志脱敏。
- 调用方持有数据库 session 时，DAO 不得擅自 commit、rollback、关闭或切换连接；跨语句原子写入由
  Service 确定事务边界。
- 初始化和清理应可重复调用；取消、连接失败和部分启动失败不能被静默吞掉。
- 日志和错误信息不得包含密码、secret、完整 token、授权 code、连接 URI 或未脱敏业务 payload。
