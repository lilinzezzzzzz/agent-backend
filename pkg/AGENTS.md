# AGENTS.md

适用于 `pkg/` 及其子目录，除非子目录有更具体的 `AGENTS.md`。

## 层职责

`pkg/` 是项目内可复用基础包，提供并发执行、数据库、日志、工具、HTTP/Redis 客户端、任务队列、
调度器、API 响应、请求上下文、认证安全、ID 生成、LLM 客户端、OSS、第三方登录、向量检索等通用能力。
这里的代码应尽量不依赖 `internal/` 业务层。

## 编码约定

- 避免从 `pkg/` 导入 `internal/`，保持基础包可复用。
- 公共函数、类和协议需要清晰类型注解。
- 新增能力优先遵循现有模块边界，不随意创建跨领域工具集合。
- 错误类型要可被上层捕获和转换，避免直接绑定具体 API 响应。
- 日志不要假设业务上下文一定存在。

## 兼容性要求

- `pkg/` 中的公开 import、类名、函数名、参数、返回值都可能被多个业务层或测试依赖。
- 删除 re-export、重命名模块、改变异常类型或序列化格式前要全仓检索调用方。
- 通用能力变更应补对应 `tests/toolkit/`、`tests/orm/`、`tests/logger/`、`tests/vector/` 等测试。

## 验证重点

- 优先运行被修改包对应的最小测试目录。
- 涉及多个使用方时补跨层测试或至少运行相关调用方测试。

## 子目录 AGENTS.md 索引

- `pkg/agents/AGENTS.md`：结构化 ReAct / Tool Calling 执行循环和 Agent 状态契约。
- `pkg/api_response/AGENTS.md`：API 响应 payload、应用错误码载体和 SSE 包装。
- `pkg/chars_matcher/AGENTS.md`：中文候选字拼音 / 字形匹配、数据文件约定。
- `pkg/celery_queue/AGENTS.md`：Celery client、任务投递和 Worker hook。
- `pkg/config_loader/AGENTS.md`：多格式配置文件加载与配置合并工具。
- `pkg/concurrency/AGENTS.md`：进程内后台任务、同步函数 offload、取消、超时和并发限制。
- `pkg/crypter/AGENTS.md`：加密算法抽象和密文兼容性。
- `pkg/database/AGENTS.md`：ORM 基类、builder、session provider。
- `pkg/decorators/AGENTS.md`：通用装饰器，零业务依赖。
- `pkg/embeddings/AGENTS.md`：Embedding contract、provider registry 和维度校验。
- `pkg/files/AGENTS.md`：异步文件读写工具。
- `pkg/http_client/AGENTS.md`：基于 httpx 的异步 HTTP 客户端封装。
- `pkg/ids/AGENTS.md`：UUIDv7 / Snowflake ID 生成。
- `pkg/llm/AGENTS.md`：OpenAI-compatible LLM 客户端、provider 能力和结构化输出契约。
- `pkg/logger/AGENTS.md`：Loguru 封装与延迟初始化协议。
- `pkg/lazy_proxy/AGENTS.md`：生命周期对象的延迟代理语义。
- `pkg/oss/AGENTS.md`：对象存储统一契约、后端注册。
- `pkg/redis/AGENTS.md`：Redis 原语客户端、JSON 编解码和分布式锁。
- `pkg/request_context/AGENTS.md`：ContextVar 请求上下文。
- `pkg/scheduler/AGENTS.md`：APScheduler 管理器。
- `pkg/security/AGENTS.md`：JWT、签名验签、密码哈希。
- `pkg/smart_types/AGENTS.md`：Pydantic smart types。
- `pkg/third_party_auth/AGENTS.md`：第三方登录策略 + 工厂扩展。
- `pkg/toolkit/AGENTS.md`：细粒度通用工具定位、兼容性和禁忌。
- `pkg/tracing/AGENTS.md`：OpenTelemetry runtime、Span 门面和进程生命周期。
- `pkg/vectors/AGENTS.md`：向量检索抽象、repository、backend；Milvus 细则见
  `pkg/vectors/backends/milvus/AGENTS.md`。
