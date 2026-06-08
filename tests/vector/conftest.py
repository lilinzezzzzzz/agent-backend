from __future__ import annotations

import sys
import types
from pathlib import Path


def _unavailable_provider(*args, **kwargs):
    raise RuntimeError("vector tests do not initialize infra providers")


database_module = types.ModuleType("internal.infra.database")
database_module.get_session = _unavailable_provider
database_module.get_read_session = _unavailable_provider
database_module.init_async_db = lambda *_, **__: None
database_module.close_async_db = lambda *_, **__: None
database_module.reset_async_db = lambda: None

redis_module = types.ModuleType("internal.infra.redis")
redis_module.redis_client = None
redis_module.get_redis = _unavailable_provider
redis_module.init_async_redis = lambda *_, **__: None
redis_module.close_async_redis = lambda *_, **__: None
redis_module.reset_async_redis = lambda: None
redis_module.__path__ = [
    str(Path(__file__).resolve().parents[2] / "internal" / "infra" / "redis")
]

redis_connection_module = types.ModuleType("internal.infra.redis.connection")
redis_connection_module.redis_client = None
redis_connection_module.get_redis = _unavailable_provider
redis_connection_module.init_async_redis = lambda *_, **__: None
redis_connection_module.close_async_redis = lambda *_, **__: None
redis_connection_module.reset_async_redis = lambda: None

infra_module = types.ModuleType("internal.infra")
infra_module.__path__ = [
    str(Path(__file__).resolve().parents[2] / "internal" / "infra")
]
infra_module.database = database_module
infra_module.redis = redis_module

sys.modules["internal.infra"] = infra_module
sys.modules["internal.infra.database"] = database_module
sys.modules["internal.infra.redis"] = redis_module
sys.modules["internal.infra.redis.connection"] = redis_connection_module
