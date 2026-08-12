import uuid

import uuid6


def uuid7_unique_id() -> uuid.UUID:
    """Generate a complete, time-sortable UUIDv7 value."""
    return uuid6.uuid7()


def uuid7_unique_str_id() -> str:
    """Generate a time-sortable UUIDv7 hex string."""
    return uuid7_unique_id().hex


def normalize_uuid7_trace_id(value: str | None) -> str | None:
    """校验并规范化前端传入的 UUIDv7 hex Trace ID。"""

    if not isinstance(value, str) or len(value) != 32:
        return None
    try:
        parsed = uuid.UUID(hex=value)
    except ValueError:
        return None
    if parsed.version != 7 or parsed.int == 0:
        return None
    return parsed.hex
