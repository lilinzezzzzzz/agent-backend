import uuid


def uuid7_unique_id() -> uuid.UUID:
    """Generate a complete, time-sortable UUIDv7 value."""
    return uuid.uuid7()


def uuid7_unique_str_id() -> str:
    """Generate a time-sortable UUIDv7 hex string."""
    return uuid7_unique_id().hex
