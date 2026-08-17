import asyncio
import sys
from unittest.mock import MagicMock

import anyio
import pytest

# --- 取消 conftest.py 的 mock ---
if "pkg.request_context" in sys.modules:
    del sys.modules["pkg.request_context"]

# --- Mock 依赖 ---
mock_logger = MagicMock()
sys.modules["pkg.async_logger"] = MagicMock()
sys.modules["pkg.async_logger"].logger = mock_logger

# --- 导入你的代码 ---
# NOTE: 必须在 mock 之后导入，因为 async_context 依赖 async_logger
from pkg.request_context import (  # noqa: E402
    clear,
    get_trace_id,
    get_user_id,
    init,
    scoped_values,
    set_trace_id,
    set_user_id,
    set_val,
)

import pkg  # noqa: E402
import pkg.request_context as request_context_module  # noqa: E402

pkg.request_context = request_context_module


# --- Fixture ---
@pytest.fixture(autouse=True)
def clean_context():
    sys.modules["pkg.request_context"] = request_context_module
    pkg.request_context = request_context_module
    clear()
    mock_logger.reset_mock()
    yield


# --- 测试用例 ---


def test_basic_lifecycle():
    """测试正常的生命周期"""
    tid = "trace-123"
    init()
    set_trace_id(tid)

    assert get_trace_id() == tid

    set_user_id(1001)
    assert get_user_id() == 1001


def test_set_trace_id_validation_error():
    """
    测试 set_trace_id 的参数校验
    """
    init()

    # 验证：传入 None 应引发 ValueError
    with pytest.raises(ValueError, match="trace_id is mandatory"):
        set_trace_id(None)

    # 验证：传入空字符串 应引发 ValueError
    with pytest.raises(ValueError, match="trace_id is mandatory"):
        set_trace_id("")

    # 验证：传入非字符串 应引发 ValueError
    with pytest.raises(ValueError, match="trace_id must be a string"):
        set_trace_id(123)


def test_get_without_init():
    """测试没有 Init 时，Get 的行为"""
    # 业务函数 get_user_id 应该抛出异常
    with pytest.raises(LookupError, match="user_id is not set"):
        get_user_id()


def test_set_without_init_raises_error():
    """测试没有 Init 时，Set 会抛出 RuntimeError"""
    from contextvars import ContextVar

    import pkg.request_context

    # 1. 保存旧的 ContextVar (避免影响其他测试)
    old_var = pkg.request_context._request_context_var

    # 2. 临时替换为一个全新的、未初始化的 ContextVar
    pkg.request_context._request_context_var = ContextVar("temp_test_ctx")

    try:
        # 3. 执行测试：直接 Set 应该抛出 RuntimeError
        with pytest.raises(RuntimeError, match="Request Context not initialized"):
            set_val("temp_key", "temp_value")

    finally:
        # 4. 恢复现场
        pkg.request_context._request_context_var = old_var


def test_scoped_values_restores_context_after_exit():
    init(trace_id="outer-trace", retained="outer-value")

    with scoped_values(trace_id="inner-trace", temporary="inner-value"):
        assert request_context_module._ctx_manager.all() == {
            "trace_id": "inner-trace",
            "retained": "outer-value",
            "temporary": "inner-value",
        }
        set_val("retained", "changed-inside-scope")

    assert request_context_module._ctx_manager.all() == {
        "trace_id": "outer-trace",
        "retained": "outer-value",
    }


def test_scoped_values_restores_nested_context_after_exception():
    init(trace_id="outer-trace")

    with scoped_values(trace_id="middle-trace"):
        with pytest.raises(RuntimeError, match="scope failed"):
            with scoped_values(trace_id="inner-trace"):
                raise RuntimeError("scope failed")

        assert get_trace_id() == "middle-trace"

    assert get_trace_id() == "outer-trace"


def test_scoped_values_restores_uninitialized_state():
    from contextvars import ContextVar

    old_var = request_context_module._request_context_var
    request_context_module._request_context_var = ContextVar("uninitialized_test_ctx")

    try:
        with scoped_values(temporary="inner-value"):
            assert request_context_module._ctx_manager.all() == {
                "temporary": "inner-value"
            }

        with pytest.raises(RuntimeError, match="Request Context not initialized"):
            set_val("after_scope", "value")
    finally:
        request_context_module._request_context_var = old_var


@pytest.mark.asyncio
async def test_async_context_isolation():
    """测试并发隔离性"""

    async def request_handler(trace_id, user_id, delay):
        init()
        set_trace_id(trace_id)
        set_user_id(user_id)
        await anyio.sleep(delay)
        return get_trace_id(), get_user_id()

    task_a = asyncio.create_task(request_handler("trace-A", 100, 0.1))
    task_b = asyncio.create_task(request_handler("trace-B", 200, 0.05))

    res_a = await task_a
    res_b = await task_b

    assert res_a == ("trace-A", 100)
    assert res_b == ("trace-B", 200)
