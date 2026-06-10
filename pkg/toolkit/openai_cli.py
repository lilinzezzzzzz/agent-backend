from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import openai
from openai import NOT_GIVEN, OpenAIError
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionAssistantMessageParam,
    ChatCompletionDeveloperMessageParam,
    ChatCompletionFunctionMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionUserMessageParam,
)
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk
from pydantic import BaseModel


class ThinkingMode(StrEnum):
    AUTO = "auto"
    ENABLED = "enabled"
    DISABLED = "disabled"


class StructuredOutputMode(StrEnum):
    NATIVE = "native"
    JSON_OBJECT = "json_object"
    NATIVE_WITH_JSON_OBJECT_FALLBACK = "native_with_json_object_fallback"


class ThinkingParamStyle(StrEnum):
    NONE = "none"
    REASONING_EFFORT = "reasoning_effort"
    EXTRA_BODY_THINKING = "extra_body_thinking"


class OpenAIClientError(RuntimeError):
    """Base exception raised by OpenAIClient helper methods."""


class StructuredOutputRefusalError(OpenAIClientError):
    """Raised when a model refuses a structured output request."""


class StructuredOutputParseError(OpenAIClientError):
    """Raised when a structured output response cannot be parsed."""


@dataclass(frozen=True)
class ProviderCapabilities:
    """OpenAI-compatible provider behavior switches."""

    name: str
    structured_output_mode: StructuredOutputMode = (
        StructuredOutputMode.NATIVE_WITH_JSON_OBJECT_FALLBACK
    )
    thinking_param_style: ThinkingParamStyle = ThinkingParamStyle.NONE
    supports_reasoning_effort: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "structured_output_mode",
            StructuredOutputMode(self.structured_output_mode),
        )
        object.__setattr__(
            self,
            "thinking_param_style",
            ThinkingParamStyle(self.thinking_param_style),
        )


PROVIDER_CAPABILITIES: dict[str, ProviderCapabilities] = {
    "openai": ProviderCapabilities(
        name="openai",
        structured_output_mode=StructuredOutputMode.NATIVE,
        thinking_param_style=ThinkingParamStyle.REASONING_EFFORT,
        supports_reasoning_effort=True,
    ),
    "deepseek": ProviderCapabilities(
        name="deepseek",
        structured_output_mode=StructuredOutputMode.JSON_OBJECT,
        thinking_param_style=ThinkingParamStyle.EXTRA_BODY_THINKING,
        supports_reasoning_effort=True,
    ),
    "mimo": ProviderCapabilities(
        name="mimo",
        structured_output_mode=StructuredOutputMode.NATIVE_WITH_JSON_OBJECT_FALLBACK,
        thinking_param_style=ThinkingParamStyle.EXTRA_BODY_THINKING,
        supports_reasoning_effort=True,
    ),
    "xiaomi": ProviderCapabilities(
        name="xiaomi",
        structured_output_mode=StructuredOutputMode.NATIVE_WITH_JSON_OBJECT_FALLBACK,
        thinking_param_style=ThinkingParamStyle.EXTRA_BODY_THINKING,
        supports_reasoning_effort=True,
    ),
    "openai_compatible": ProviderCapabilities(name="openai_compatible"),
}


class OpenAIClient:
    """Async OpenAI-compatible chat completion client."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: int = 180,
        api_key: str = "password",
        provider: str | None = None,
        provider_capabilities: ProviderCapabilities | None = None,
    ):
        self.base_url = base_url
        self.model = model
        self.provider = self._normalize_provider(provider) or self._infer_provider(
            base_url, model
        )
        self.provider_capabilities = (
            provider_capabilities or self._get_provider_capabilities(self.provider)
        )
        self.client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

    @staticmethod
    def _normalize_provider(provider: str | None) -> str | None:
        if not provider:
            return None
        return provider.lower().replace("-", "_")

    @classmethod
    def _infer_provider(cls, base_url: str, model: str) -> str:
        value = f"{base_url} {model}".lower()
        if "deepseek" in value:
            return "deepseek"
        if "xiaomimimo" in value or "mimo" in value:
            return "mimo"
        if "openai" in value:
            return "openai"
        return "openai_compatible"

    @classmethod
    def _get_provider_capabilities(cls, provider: str) -> ProviderCapabilities:
        return PROVIDER_CAPABILITIES.get(
            provider, PROVIDER_CAPABILITIES["openai_compatible"]
        )

    @staticmethod
    def _normalize_thinking_mode(
        thinking: bool | ThinkingMode | str | None,
    ) -> ThinkingMode | None:
        if thinking is None:
            return None
        if thinking is True:
            return ThinkingMode.ENABLED
        if thinking is False:
            return ThinkingMode.DISABLED
        if isinstance(thinking, ThinkingMode):
            return thinking
        try:
            return ThinkingMode(thinking)
        except ValueError as e:
            raise ValueError(
                "thinking must be one of True, False, 'auto', 'enabled', or 'disabled'"
            ) from e

    @staticmethod
    def _convert_messages(
        messages: Sequence[Mapping[str, Any]],
    ) -> list[ChatCompletionMessageParam]:
        """Convert loose message mappings to OpenAI SDK chat message params."""
        if not messages:
            return []

        def _require_content(role_name: str, message_content: Any, index: int) -> Any:
            if message_content is None:
                raise ValueError(
                    f"{role_name} message[{index}] missing required 'content'"
                )
            return message_content

        converted: list[ChatCompletionMessageParam] = []
        for i, msg in enumerate(messages):
            role = msg.get("role")
            content = msg.get("content")

            if not role:
                raise ValueError(f"Invalid message[{i}] missing role: {msg}")

            if role == "user":
                converted.append(
                    ChatCompletionUserMessageParam(
                        role="user", content=_require_content("user", content, i)
                    )
                )
            elif role == "system":
                converted.append(
                    ChatCompletionSystemMessageParam(
                        role="system", content=_require_content("system", content, i)
                    )
                )
            elif role == "assistant":
                assistant_msg: ChatCompletionAssistantMessageParam = {
                    "role": "assistant"
                }
                if content is not None:
                    assistant_msg["content"] = content
                if msg.get("tool_calls") is not None:
                    assistant_msg["tool_calls"] = msg["tool_calls"]
                if msg.get("function_call") is not None:
                    assistant_msg["function_call"] = msg["function_call"]
                if (
                    "content" not in assistant_msg
                    and "tool_calls" not in assistant_msg
                    and "function_call" not in assistant_msg
                ):
                    raise ValueError(
                        f"assistant message[{i}] must provide at least one of content/tool_calls/function_call"
                    )
                converted.append(assistant_msg)
            elif role == "developer":
                developer_msg: ChatCompletionDeveloperMessageParam = {
                    "role": "developer",
                    "content": _require_content("developer", content, i),
                }
                if msg.get("name") is not None:
                    developer_msg["name"] = msg["name"]
                converted.append(developer_msg)
            elif role == "tool":
                if not msg.get("tool_call_id"):
                    raise ValueError(
                        f"Tool message[{i}] missing required 'tool_call_id'"
                    )
                converted.append(
                    ChatCompletionToolMessageParam(
                        role="tool",
                        content=_require_content("tool", content, i),
                        tool_call_id=msg["tool_call_id"],
                    )
                )
            elif role == "function":
                if not msg.get("name"):
                    raise ValueError(f"Function message[{i}] missing required 'name'")
                converted.append(
                    ChatCompletionFunctionMessageParam(
                        role="function",
                        content=content,
                        name=msg["name"],
                    )
                )
            else:
                raise ValueError(f"Invalid role '{role}' at message[{i}]")

        return converted

    def _get_thinking_params(
        self,
        *,
        thinking: bool | ThinkingMode | str | None = None,
        reasoning_effort: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Convert normalized thinking options into provider-specific API params."""
        thinking_mode = self._normalize_thinking_mode(thinking)
        params: dict[str, Any] = {}
        body = dict(extra_body or {})

        if (
            self.provider_capabilities.thinking_param_style
            == ThinkingParamStyle.EXTRA_BODY_THINKING
            and thinking_mode is not None
            and thinking_mode in {ThinkingMode.ENABLED, ThinkingMode.DISABLED}
        ):
            body["thinking"] = {"type": thinking_mode.value}
        if (
            self.provider_capabilities.supports_reasoning_effort
            and reasoning_effort is not None
            and thinking_mode != ThinkingMode.DISABLED
        ):
            params["reasoning_effort"] = reasoning_effort

        if body:
            params["extra_body"] = body
        return params

    def _get_completion_params(
        self,
        messages: Sequence[Mapping[str, Any]],
        stream: bool,
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        n: int | None = None,
        frequency_penalty: float | None = None,
        thinking: bool | ThinkingMode | str | None = None,
        reasoning_effort: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build chat.completions request params."""
        extra_body = kwargs.pop("extra_body", None)
        params = {
            "model": self.model,
            "messages": self._convert_messages(messages),
            "temperature": temperature if temperature is not None else NOT_GIVEN,
            "max_tokens": max_tokens if max_tokens is not None else NOT_GIVEN,
            "top_p": top_p if top_p is not None else NOT_GIVEN,
            "n": n if n is not None else NOT_GIVEN,
            "frequency_penalty": (
                frequency_penalty if frequency_penalty is not None else NOT_GIVEN
            ),
            "stream": stream,
            **kwargs,
        }
        params.update(
            self._get_thinking_params(
                thinking=thinking,
                reasoning_effort=reasoning_effort,
                extra_body=extra_body,
            )
        )
        return params

    async def chat_completion(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        n: int | None = None,
        frequency_penalty: float | None = None,
        thinking: bool | ThinkingMode | str | None = None,
        reasoning_effort: str | None = None,
        **kwargs: Any,
    ) -> ChatCompletion:
        """Create a non-streaming chat completion."""
        params = self._get_completion_params(
            messages,
            False,
            max_tokens,
            temperature,
            top_p,
            n,
            frequency_penalty,
            thinking,
            reasoning_effort,
            **kwargs,
        )
        return await self.client.chat.completions.create(**params)

    async def chat_completion_structured[StructuredOutputT: BaseModel](
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_model: type[StructuredOutputT],
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        n: int | None = None,
        frequency_penalty: float | None = None,
        thinking: bool | ThinkingMode | str | None = None,
        reasoning_effort: str | None = None,
        **kwargs: Any,
    ) -> StructuredOutputT:
        """Create a structured chat completion and return the parsed Pydantic model."""
        audit_hook = kwargs.pop("_audit_hook", None)
        if "response_format" in kwargs:
            raise ValueError(
                "Use response_model instead of response_format for structured completions"
            )
        if "stream" in kwargs:
            raise ValueError("chat_completion_structured does not support stream")

        if (
            self.provider_capabilities.structured_output_mode
            == StructuredOutputMode.JSON_OBJECT
        ):
            return await self._chat_completion_json_object(
                messages=messages,
                response_model=response_model,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                n=n,
                frequency_penalty=frequency_penalty,
                thinking=thinking,
                reasoning_effort=reasoning_effort,
                audit_hook=audit_hook,
                **kwargs,
            )

        try:
            return await self._chat_completion_native_structured(
                messages=messages,
                response_model=response_model,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                n=n,
                frequency_penalty=frequency_penalty,
                thinking=thinking,
                reasoning_effort=reasoning_effort,
                audit_hook=audit_hook,
                **kwargs,
            )
        except OpenAIError:
            if (
                self.provider_capabilities.structured_output_mode
                != StructuredOutputMode.NATIVE_WITH_JSON_OBJECT_FALLBACK
            ):
                raise
            return await self._chat_completion_json_object(
                messages=messages,
                response_model=response_model,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                n=n,
                frequency_penalty=frequency_penalty,
                thinking=thinking,
                reasoning_effort=reasoning_effort,
                audit_hook=audit_hook,
                **kwargs,
            )

    async def _chat_completion_native_structured[StructuredOutputT: BaseModel](
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_model: type[StructuredOutputT],
        max_tokens: int | None,
        temperature: float | None,
        top_p: float | None,
        n: int | None,
        frequency_penalty: float | None,
        thinking: bool | ThinkingMode | str | None,
        reasoning_effort: str | None,
        audit_hook: Callable[[Any], None] | None = None,
        **kwargs: Any,
    ) -> StructuredOutputT:
        params = {
            **self._get_completion_params(
                messages,
                False,
                max_tokens,
                temperature,
                top_p,
                n,
                frequency_penalty,
                thinking,
                reasoning_effort,
                **kwargs,
            ),
            "response_format": response_model,
        }
        params.pop("stream", None)

        response = await self.client.chat.completions.parse(**params)
        if audit_hook is not None:
            audit_hook(response)
        message = response.choices[0].message
        if message.refusal:
            raise StructuredOutputRefusalError(message.refusal)
        if message.parsed is None:
            raise StructuredOutputParseError(
                "OpenAI structured response was not parsed"
            )
        return message.parsed

    async def _chat_completion_json_object[StructuredOutputT: BaseModel](
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_model: type[StructuredOutputT],
        max_tokens: int | None,
        temperature: float | None,
        top_p: float | None,
        n: int | None,
        frequency_penalty: float | None,
        thinking: bool | ThinkingMode | str | None,
        reasoning_effort: str | None,
        audit_hook: Callable[[Any], None] | None = None,
        **kwargs: Any,
    ) -> StructuredOutputT:
        params = self._get_completion_params(
            messages,
            False,
            max_tokens,
            temperature,
            top_p,
            n,
            frequency_penalty,
            thinking,
            reasoning_effort,
            **kwargs,
        )
        params["response_format"] = {"type": "json_object"}
        params.pop("stream", None)

        response = await self.client.chat.completions.create(**params)
        if audit_hook is not None:
            audit_hook(response)
        if not response.choices:
            raise StructuredOutputParseError(
                "OpenAI json_object response has no choices"
            )
        content = response.choices[0].message.content
        if not content:
            raise StructuredOutputParseError(
                "OpenAI json_object response returned empty content"
            )
        return response_model.model_validate_json(content)

    async def chat_completion_stream(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        n: int | None = None,
        frequency_penalty: float | None = None,
        thinking: bool | ThinkingMode | str | None = None,
        reasoning_effort: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Create a streaming chat completion and yield text deltas."""
        params = self._get_completion_params(
            messages,
            True,
            max_tokens,
            temperature,
            top_p,
            n,
            frequency_penalty,
            thinking,
            reasoning_effort,
            **kwargs,
        )
        response = await self.client.chat.completions.create(**params)

        async for chunk in response:
            if not isinstance(chunk, ChatCompletionChunk):
                continue
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta
            if delta.content is not None:
                yield delta.content
