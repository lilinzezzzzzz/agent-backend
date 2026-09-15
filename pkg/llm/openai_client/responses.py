import json

from collections.abc import AsyncGenerator, Callable
from typing import Any

import openai
from openai.types.responses import (
    Response,
    ResponseInputParam,
    ResponseStreamEvent,
    ResponseFormatTextConfigParam,
)
from openai.types.shared_params import Reasoning
from pydantic import BaseModel, ValidationError

from pkg.llm.errors import (
    ResponseFailedError,
    ResponseIncompleteError,
    StructuredOutputParseError,
    StructuredOutputRefusalError,
)
from pkg.llm.openai_client._base import BaseOpenAIClient
from pkg.llm.types import StructuredOutputMode


class OpenAIResponsesClient(BaseOpenAIClient):
    """Responses 客户端；保留 SDK 的 input items、工具调用和流式事件。

    默认不存储服务端会话。MiMo 使用 JSON object 并在本地校验 schema；
    其他服务默认发送非 strict JSON schema，以支持含动态字典的业务模型。
    不自动降级到 Chat Completions，也不在请求失败后重试另一种输出格式。
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: int = 180,
        api_key: str = "password",
        provider: str | None = None,
        *,
        client: openai.AsyncOpenAI | None = None,
        structured_output_mode: StructuredOutputMode | None = None,
    ):
        super().__init__(base_url, model, timeout, api_key, provider, client)
        self.structured_output_mode = StructuredOutputMode(
            structured_output_mode
            or (
                StructuredOutputMode.JSON_OBJECT
                if self.provider in {"mimo", "xiaomi"}
                else StructuredOutputMode.NATIVE
            )
        )
        if (
            self.structured_output_mode
            == StructuredOutputMode.NATIVE_WITH_JSON_OBJECT_FALLBACK
        ):
            raise ValueError("Responses does not support automatic format fallback")

    def _get_response_params(
        self,
        *,
        input: str | ResponseInputParam,
        instructions: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        reasoning: Reasoning | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        reserved = {
            "messages",
            "max_tokens",
            "response_format",
            "thinking",
            "reasoning_effort",
            "n",
            "frequency_penalty",
            "stream",
            "model",
            "text_format",
        }.intersection(kwargs)
        if reserved:
            raise ValueError(
                f"Unsupported Responses parameters: {', '.join(sorted(reserved))}"
            )
        params: dict[str, Any] = {
            "model": self.model,
            "input": input,
            "store": False,
            **kwargs,
        }
        for name, value in (
            ("instructions", instructions),
            ("max_output_tokens", max_output_tokens),
            ("temperature", temperature),
            ("reasoning", reasoning),
        ):
            if value is not None:
                params[name] = value
        return params

    async def response(
        self,
        *,
        input: str | ResponseInputParam,
        instructions: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        reasoning: Reasoning | None = None,
        **kwargs: Any,
    ) -> Response:
        """返回完整 Response，调用方可读取文本、工具调用及 usage。"""
        params = self._get_response_params(
            input=input,
            instructions=instructions,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            reasoning=reasoning,
            **kwargs,
        )
        return await self.client.responses.create(**params, stream=False)

    async def response_structured[StructuredOutputT: BaseModel](
        self,
        *,
        input: str | ResponseInputParam,
        response_model: type[StructuredOutputT],
        instructions: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        reasoning: Reasoning | None = None,
        _audit_hook: Callable[[Response], None] | None = None,
        **kwargs: Any,
    ) -> StructuredOutputT:
        """先保留原始审计响应，再检查完成状态、拒绝及 Pydantic schema。"""
        if "text" in kwargs:
            raise ValueError(
                "Use response_model instead of text for structured responses"
            )
        schema = response_model.model_json_schema()
        text_format: ResponseFormatTextConfigParam
        if self.structured_output_mode == StructuredOutputMode.JSON_OBJECT:
            text_format = {"type": "json_object"}
            schema_instruction = (
                "Return only a JSON object matching this JSON schema:\n"
                + json.dumps(schema, ensure_ascii=False)
            )
            instructions = "\n\n".join(filter(None, [instructions, schema_instruction]))
        else:
            text_format = {
                "type": "json_schema",
                "name": response_model.__name__,
                "schema": schema,
                "strict": False,
            }
        response = await self.response(
            input=input,
            instructions=instructions,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            reasoning=reasoning,
            text={"format": text_format},
            **kwargs,
        )
        if _audit_hook is not None:
            _audit_hook(response)
        self._require_completed(response)
        for item in response.output:
            if item.type == "message":
                for content in item.content:
                    if content.type == "refusal":
                        raise StructuredOutputRefusalError(content.refusal)
        if not response.output_text:
            raise StructuredOutputParseError("Response has no output text")
        try:
            return response_model.model_validate_json(response.output_text)
        except ValidationError as exc:
            raise StructuredOutputParseError(
                "Response does not match the requested schema"
            ) from exc

    async def response_stream(
        self,
        *,
        input: str | ResponseInputParam,
        instructions: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        reasoning: Reasoning | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ResponseStreamEvent, None]:
        """逐个返回完整事件；消费方负责处理 completed/incomplete/failed。

        提前停止消费时应关闭本生成器（例如使用 contextlib.aclosing）。
        底层 HTTP stream 在正常结束、异常或取消时关闭。
        """
        params = self._get_response_params(
            input=input,
            instructions=instructions,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            reasoning=reasoning,
            **kwargs,
        )
        stream = await self.client.responses.create(**params, stream=True)
        terminal_seen = False
        try:
            async for event in stream:
                if event.type in {
                    "response.completed",
                    "response.incomplete",
                    "response.failed",
                    "error",
                }:
                    terminal_seen = True
                yield event
            if not terminal_seen:
                raise ResponseIncompleteError(
                    "Response stream ended without a terminal event"
                )
        finally:
            await stream.close()

    @staticmethod
    def _require_completed(response: Response) -> None:
        if response.error is not None or response.status == "failed":
            raise ResponseFailedError("Response generation failed")
        if response.status != "completed":
            reason = (
                response.incomplete_details.reason
                if response.incomplete_details
                else response.status
            )
            raise ResponseIncompleteError(f"Response is not complete: {reason}")
