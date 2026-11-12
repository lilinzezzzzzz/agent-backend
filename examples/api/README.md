# 独立 API 演示

本 app 提供请求校验、异常注入、后台任务上下文与 SSE 超时演示。常规入口
`entrypoints.api:app` 不注册这些路由；demo app 复用 `internal.app.create_app()` 的配置、认证、
middleware 和 lifespan，再额外挂载实验接口。应用运行时代码不依赖 `examples/`。

## 启动

在仓库根目录准备依赖、配置与服务：

- 执行 `uv sync --group dev`。
- 按主 README 准备根 `.env` 的 `APP_ENV`、对应 `configs/app.{APP_ENV}.toml` 和根 `.secrets`。
- 配置已初始化的 PostgreSQL 与 Redis；启用 tracing 时还需准备配置中的 OTLP Receiver。
  demo app 的 lifespan 与常规 app 相同，不提供绕过资源初始化的模式。

使用独立的本机端口启动：

```bash
uv run uvicorn examples.api.app:create_demo_app --factory --host 127.0.0.1 --port 8001
```

默认仅绑定 loopback；文档入口沿用 `[app]` 的开关与路径配置，与 `debug` 独立。
默认启用 `app.docs_enabled` 时可访问 `/docs`。示例请求：

```bash
curl -X POST http://127.0.0.1:8001/v1/public/test/test_validation_error \
  -H 'Content-Type: application/json' \
  -d '{"name":"demo","age":20,"email":"demo@example.com"}'
```

有效参数返回原有成功信封；非法参数、应用错误和意外错误仍由共享异常处理链转换。启动成功及本地请求
不代表 tracing 已持久化，也不代表外部服务集成已验收。

## 实验接口

| Method | 路径 | 用途 |
| --- | --- | --- |
| POST | `/v1/public/test/test_validation_error` | 校验错误信封 |
| GET | `/v1/public/test/test_raise_exception` | 注入通用异常 |
| GET | `/v1/public/test/test_raise_app_exception` | 注入应用异常 |
| GET | `/v1/public/test/test_contextvars_on_asyncio_task` | 后台任务上下文传播 |
| GET | `/v1/public/test/test/sse-stream` | SSE 流式响应 |
| GET | `/v1/public/test/chat/sse-stream/timeout` | 单 chunk 超时 |

这些接口在 demo app 中保持原路径和行为，属于 `/v1/public` 匿名边界。它们会主动产生错误、等待和
后台任务，供本地开发演示使用；不要把 demo app 作为常规服务部署入口。

## 验证

```bash
uv run pytest tests/api/test_demo_app.py tests/api/test_route_registration.py -q
```

测试验证常规应用不再暴露实验路由、demo 装配保留原路径，以及校验和错误信封；测试不启动外部依赖。
