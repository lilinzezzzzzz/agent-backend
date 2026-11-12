"""API 用例的显式请求身份，使用真实 ContextVar，不替换上下文模块。"""

from collections.abc import Generator
from uuid import UUID

import pytest

from pkg.request_context import scoped_values


@pytest.fixture(autouse=True)
def api_request_context(isolate_request_context: None) -> Generator[None, None, None]:
    with scoped_values(
        user_id=UUID("00000000-0000-7000-8000-000000000999"),
        trace_id="test-trace-id",
    ):
        yield
