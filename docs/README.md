# 文档索引

`docs/` 按主题分组，少量通用方案文档保留在根目录。

## `auth/`

认证、登录和账号接入相关文档。

- [认证模块使用指南](auth/auth_module_guide.md)
- [第三方登录集成指南](auth/third_party_login_guide.md)

## `celery/`

Celery 任务状态、幂等、取消和后台收敛机制相关文档。

- [Celery PostgreSQL 状态机设计](celery/CELERY_IDEMPOTENCY_PG_DESIGN.md)

## `development/`

开发工具、编码实践和文档写作规范。

- [uv 工作流最佳实践指南](development/uv_use_guide.md)
- [Python `@dataclass` 实战指南](development/dataclass_use_guide.md)
- [乐观锁、悲观锁与 Redis 分布式锁选型指南](development/lock_selection_guide.md)

## `logging/`

应用日志输出边界和部署环境的轮转、保留与持久化方案。

- [日志输出与轮转部署方案](logging/log_rotation.md)

## `opentelemetry/`

日志、指标、分布式追踪和可观测性数据传输相关文档。

- [OpenTelemetry 上下文传播与 OTLP 上报机制](opentelemetry/opentelemetry_context_and_otlp.md)
- [当前项目 OpenTelemetry 接入与可观测性数据持久化](opentelemetry/project_integration_and_persistence.md)

## `../plans/`

按实施状态归档的开发计划，完整索引见 [开发计划索引](../plans/README.md)。

- [未完成计划](../plans/pending/README.md)
- [已完成计划](../plans/completed/README.md)

## `agent/`

Agent 运行时、记忆和上下文工程方案。

- [Agent 记忆与 200k 上下文工程方案](agent/agent_memory_context_design.md)
- [AgentScope 2.0 Agent Service 接入方案](agent/agentscope_service_integration.md)
  - 官方文档：[AgentScope 2.0 中文文档](https://docs.agentscope.io/zh/v2)
  - 源码仓库：[agentscope-ai/agentscope](https://github.com/agentscope-ai/agentscope)

## `rag/`

RAG 工程架构、技术选型和 Agentic RAG 方案。

- [RAG 工程分类与技术选型](rag/rag-engineering-classification-selection.md)
- [Lightweight Agentic RAG 技术方案](rag/lightweight-agentic-rag.md)
- [知识库 / 文档 / Chunk ORM 管理后台技术方案](rag/kb_orm_admin_design.md)
- [PDF 文档解析与入库技术方案](rag/pdf_ingestion_design.md)

## `ai-platform/`

AI 平台和算力调度相关架构方案。

- [AI 中台数字人算力动态调度](ai-platform/AI中台数字人动态算力调度.md)
