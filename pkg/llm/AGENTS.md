# AGENTS.md

适用于 `pkg/llm/`。父级约束见 `pkg/AGENTS.md`。

## 模块职责

OpenAI-compatible LLM 客户端、provider 能力描述、思考参数适配、结构化输出解析和相关错误类型。

## 编码约定

- 不依赖 `internal/`；业务默认 provider 选择和配置装配放在 `internal/infra/llm/`。
- 对外优先从 `pkg.llm` 导出稳定符号；新增公开类、异常或枚举时同步 `pkg/llm/__init__.py`。
- provider 差异通过 `ProviderCapabilities` 表达，避免把 provider 名称判断散落到调用方。
- 保持 OpenAI-compatible Chat Completions 调用的错误类型、方法签名和返回结构稳定；破坏性变更需要同步调用方和测试。
- 不在模块 import 时读取配置、发起网络请求或初始化业务上下文。

## 验证重点

- 改 OpenAI-compatible client 时优先运行 `tests/llm/test_openai_compatible.py`。
- 涉及默认 LLM provider 装配时同时运行 `tests/infra/test_llm_client.py`。
- 外部真实模型调用测试必须可跳过或依赖显式环境变量，不要提交真实密钥。
