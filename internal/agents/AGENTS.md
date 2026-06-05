# AGENTS.md

适用于 `internal/agents/` 及其子目录。

## 层职责

本目录承载业务 Agent 的实现侧适配：LLM decision maker、router、prompt、builder 和
`StructuredTool` 定义。通用 Agent 执行循环、状态记录和工具协议来自 `pkg/agents/`。

业务用例入口仍放在 `internal/services/agents/`，HTTP contract 仍放在
`internal/controllers/` 和 `internal/schemas/`。

## 编码约定

- 不在本层实现 Controller、API schema、响应信封或 Service DTO。
- 不在本层直接访问数据库、Redis、Celery 或第三方业务系统；工具需要业务能力时，通过
  Service 或明确协议注入。
- 业务 Agent 按领域放在 `internal/agents/<domain>/`，领域目录内通常包含
  `builder.py`、`prompt.py`、`tools.py` 和必要 README。
- Builder 负责组装 `ReActAgent`、decision maker、工具和 `max_steps`；依赖通过构造函数显式注入，
  便于测试替换。
- Prompt 只表达工具路由、禁止事项、输出契约和回答边界，不承载权限、幂等、事务或状态机规则。
- Tool 必须使用 `pkg.agents.StructuredTool`，显式声明工具名、描述、参数 schema 和 handler。
- Tool handler 负责轻量参数读取和错误工具结果；复杂校验、权限、幂等和副作用提交必须下沉到
  Service / Workflow。
- Router 只决定业务域，不直接回答用户问题；不确定时选择 unsupported 或等价降级路径。
- 新增业务域时，同步检查统一路由、Service 入口、API schema、测试和领域 README。

## 副作用边界

- ReAct 可以准备可丢弃的临时副作用，例如保存短期 pending action。
- 真实业务副作用必须由 Service / Workflow 在确认路径执行；确认路径应绕过 LLM，并使用服务端可信状态。
- `confirmation_token`、`idempotency_key`、执行锁、事务、outbox 和补偿逻辑不应由 prompt 决定。

## 文档边界

- `AGENTS.md` 写未来代码修改必须遵守的规则。
- `README.md` 写业务流程、设计解释、调用方式和字段语义。
- 不要在 `AGENTS.md` 中复制 README 的完整流程图、字段表或业务背景；只保留维护约束和同步点。

## 验证重点

- 修改通用 LLM decision maker 或 router 时，优先运行：

  ```bash
  uv run pytest tests/agents/test_llm_decision.py tests/agents/test_react.py -q
  uv run ruff check internal/agents tests/agents
  ```

- 修改某个业务 Agent 时，补跑对应业务 service / API 测试。
- 修改 prompt、工具名、工具参数或 router 枚举时，同步检查结构化输出测试和 OpenAPI contract。
