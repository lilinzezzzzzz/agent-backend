from enum import StrEnum


class ThinkingMode(StrEnum):
    """Provider-independent thinking mode selector."""

    AUTO = "auto"
    ENABLED = "enabled"
    DISABLED = "disabled"


class StructuredOutputMode(StrEnum):
    """Structured output strategy supported by a provider."""

    NATIVE = "native"
    JSON_OBJECT = "json_object"
    NATIVE_WITH_JSON_OBJECT_FALLBACK = "native_with_json_object_fallback"


class ThinkingParamStyle(StrEnum):
    """Request parameter style for provider-specific thinking controls."""

    NONE = "none"
    REASONING_EFFORT = "reasoning_effort"
    EXTRA_BODY_THINKING = "extra_body_thinking"
