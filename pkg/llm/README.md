# LLM 客户端

`openai_client/` 封装 OpenAI SDK 的三个独立 API。公共基类只负责 SDK 初始化、provider 标识和连接生命周期。

- `OpenAIResponsesClient`：应用的 Router、结构化 ReAct Agent、RAG 和审计包装统一使用。
- `OpenAIEmbeddingsClient`：统一实现 `/embeddings` 调用和 `Embedder` 接口，负责顺序、数量及有效维度校验。embedding 使用独立的 `EMBEDDING_*` 配置和连接。
- `OpenAIChatCompletionsClient`：可选的 Chat Completions 实现，应用默认工厂不使用。
- 已移除旧 `pkg.llm.openai_compatible` 路径及 `OpenAIClient` 名称，没有兼容别名或跨 API fallback。
- `ChatCompletionsCapabilities` / `CHAT_COMPLETIONS_CAPABILITIES` 只描述 Chat Completions 的 provider 策略。

## Responses 用法

以下示例需要支持 Responses 的服务地址、模型和凭据；配置由应用注入，包内不读取环境文件。

```python
from contextlib import aclosing

from pkg.llm.openai_client import OpenAIResponsesClient

async with OpenAIResponsesClient(
    base_url=base_url, model=model, api_key=api_key, provider=provider,
) as client:
    response = await client.response(input="你好", max_output_tokens=256)
    # response 保留原生 SDK 对象，使用结果前检查 status/error。
    if response.status == "completed":
        print(response.output_text)

    async with aclosing(client.response_stream(input="你好")) as events:
        async for event in events:
            if event.type == "response.output_text.delta":
                print(event.delta, end="")
            elif event.type in {"response.failed", "response.incomplete", "error"}:
                raise RuntimeError("模型响应未成功完成")
```

`response_stream()` 返回完整 SDK 事件，包含工具参数、推理、文本和完成状态。调用方必须处理失败及截断事件；
没有终止事件就结束的流会抛出 `ResponseIncompleteError`。提前退出时用 `aclosing` 关闭生成器。
自建 SDK 连接由客户端关闭；通过 `client=` 注入的 SDK 连接由注入方关闭。

请求参数使用 `input`、`instructions`、`max_output_tokens`、`reasoning={"effort": ...}`。
`input` 接受字符串或 SDK input items，工具结果使用 `function_call_output` 和匹配的 `call_id`。
完整 `output` 保留工具调用、推理条目及 usage，不转换为 Chat message。
默认 `store=False`；需要服务端续接时显式传入 `store=True` 等参数，并核实服务支持情况。

## 结构化输出

`response_structured(input=..., response_model=YourModel)` 返回通过 Pydantic 校验的模型。

- 默认 `NATIVE`：发送 `text.format.type=json_schema`，使用 Pydantic JSON schema 和 `strict=False`。
  当前 ReAct 动作包含动态 `args` 字典，不适合强制转换成严格封闭对象。最终仍执行本地 schema 校验，
  不能将非 strict 模式描述为服务端严格保证。
- MiMo / Xiaomi 默认 `JSON_OBJECT`：发送 `text.format.type=json_object`，将 schema 加入 `instructions`，
  本地校验结果。可用构造参数 `structured_output_mode` 显式选择服务实际支持的模式。
- 不自动重试其他格式。HTTP 错误交给 SDK；失败、未完成、拒绝、空文本及 schema 不匹配不会返回业务模型。
- `_audit_hook` 在状态检查及本地解析前接收完整原始 Response，以便审计保留失败响应；现有审计继续递归脱敏。

应用继续使用结构化 ReAct：模型输出动作，再由已有执行循环调用本地工具。此次 API 迁移没有改为原生
function-calling Agent，也没有改变确认执行的权限和副作用边界。

## Provider 与验证

现有 `LLM_DEFAULT_PROVIDER` 和各 provider 的 URL / model / secret 配置入口不变，不增加 shell 环境变量兜底。
MiMo 文档列出当前配置的 `mimo-v2.5-pro` 支持 `/v1/responses`；MiMo 用 `reasoning.effort=none` 关闭思考。
DeepSeek 也提供 Responses，但模型可用名称和参数支持需要按其最新文档及实际服务确认。
两者的 Responses 会话能力不能视为与 OpenAI 完全一致，应用目前每次发送完整输入，不依赖服务端会话。

- [OpenAI 迁移指南](https://developers.openai.com/api/docs/guides/migrate-to-responses)
- [MiMo Responses 文档](https://mimo.mi.com/docs/en-US/api/chat/responses)
- [DeepSeek Responses 文档](https://api-docs.deepseek.com/guides/responses_api/)

本地验证（HTTP mock 经过实际 OpenAI SDK，不访问外部模型）：

```bash
uv run --group dev pytest tests/llm tests/agents tests/infra/test_llm_client.py tests/services/test_agent_audit.py tests/services/test_rag_service.py tests/api/test_agent.py -m 'not integration'
```

真实模型测试标记为 `integration`，需单独准备服务与凭据；本地 mock 通过不等于服务端兼容性已验证。

## Embedding 客户端

通过 `pkg.embeddings.create_embedder()` 直接创建 `OpenAIEmbeddingsClient`，provider 配置值仍为 `openai_compatible`。
实现集中在 `pkg/llm/openai_client/embeddings.py`，删除独立的适配类及旧模块。
`pkg/embeddings` 仅保留通用接口、异常与 registry；工厂延迟导入实现，避免循环依赖。
`embeddings()` 返回原始 SDK 响应，使用显式 `dimensions`；`embed_texts()` / `embed_text()` / `embed_query()`
返回经过校验的向量，使用实例默认 `dimension` 或调用时的覆盖值。
单条、查询和批量接口复用同一条校验路径；空批次不发送网络请求，批次超过 2048 条时拒绝并要求调用方分批。
调用时传入的 `dimension` 同时决定请求参数和结果校验，不修改实例默认维度。
SDK 超时、provider 错误和取消原样传播，响应校验使用 embedding 专用异常。
使用 `async with embedder` 或 `await embedder.close()` 关闭自建连接；注入的客户端由调用方关闭。
原来的 `token_limit` / `offline_token_count` 仅保存而未生效，本次移除这些参数，不承诺本地 token 计数或截断。
具体模型 token 限制由服务端验证，上层应根据模型限制切块。
