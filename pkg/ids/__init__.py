import secrets
import threading
import time
import uuid


_UUID7_TIMESTAMP_MASK = (1 << 48) - 1
_last_uuid7_timestamp_ms = -1
_uuid7_lock = threading.Lock()


def _uuid7_compat() -> uuid.UUID:
    """Generate an RFC 9562 UUIDv7 on Python versions before 3.14."""

    global _last_uuid7_timestamp_ms

    timestamp_ms = time.time_ns() // 1_000_000
    with _uuid7_lock:
        timestamp_ms = max(timestamp_ms, _last_uuid7_timestamp_ms + 1)
        _last_uuid7_timestamp_ms = timestamp_ms

    random_bits = secrets.randbits(74)
    uuid_int = (timestamp_ms & _UUID7_TIMESTAMP_MASK) << 80
    uuid_int |= 0x7 << 76
    uuid_int |= (random_bits >> 62) << 64
    uuid_int |= 0b10 << 62
    uuid_int |= random_bits & ((1 << 62) - 1)
    return uuid.UUID(int=uuid_int)


def uuid7_unique_id() -> uuid.UUID:
    """Generate a complete, time-sortable UUIDv7 value."""

    stdlib_uuid7 = getattr(uuid, "uuid7", None)
    if stdlib_uuid7 is not None:
        return stdlib_uuid7()
    return _uuid7_compat()


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
