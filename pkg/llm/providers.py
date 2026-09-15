from dataclasses import dataclass

from pkg.llm.types import StructuredOutputMode, ThinkingParamStyle


@dataclass(frozen=True)
class ChatCompletionsCapabilities:
    """Chat Completions provider behavior switches."""

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


CHAT_COMPLETIONS_CAPABILITIES: dict[str, ChatCompletionsCapabilities] = {
    "openai": ChatCompletionsCapabilities(
        name="openai",
        structured_output_mode=StructuredOutputMode.NATIVE,
        thinking_param_style=ThinkingParamStyle.REASONING_EFFORT,
        supports_reasoning_effort=True,
    ),
    "deepseek": ChatCompletionsCapabilities(
        name="deepseek",
        structured_output_mode=StructuredOutputMode.JSON_OBJECT,
        thinking_param_style=ThinkingParamStyle.EXTRA_BODY_THINKING,
        supports_reasoning_effort=True,
    ),
    "mimo": ChatCompletionsCapabilities(
        name="mimo",
        structured_output_mode=StructuredOutputMode.NATIVE_WITH_JSON_OBJECT_FALLBACK,
        thinking_param_style=ThinkingParamStyle.EXTRA_BODY_THINKING,
        supports_reasoning_effort=True,
    ),
    "xiaomi": ChatCompletionsCapabilities(
        name="xiaomi",
        structured_output_mode=StructuredOutputMode.NATIVE_WITH_JSON_OBJECT_FALLBACK,
        thinking_param_style=ThinkingParamStyle.EXTRA_BODY_THINKING,
        supports_reasoning_effort=True,
    ),
    "openai_compatible": ChatCompletionsCapabilities(name="openai_compatible"),
}
