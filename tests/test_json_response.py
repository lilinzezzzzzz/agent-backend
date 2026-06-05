import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from internal.schemas import BaseListResponse, BaseResponse
from pkg.toolkit.json import orjson_dumps, orjson_dumps_bytes, orjson_loads
from pkg.toolkit.response import (
    AppError,
    ResponsePayload,
    error_response,
    success_list_response,
    success_response,
    wrap_sse_data,
    wrap_sse_event,
)

# 尝试导入 numpy，用于测试科学计算场景的数据兼容性
try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore
    HAS_NUMPY = False

# =========================================================
# 0. Fixtures & Setup (测试脚手架)
# =========================================================


class UserSchema(BaseModel):
    id: int
    name: str
    meta: dict[str, Any] = Field(default_factory=dict)


@pytest.fixture
def sample_uuid() -> uuid.UUID:
    return uuid.UUID("550e8400-e29b-41d4-a716-446655440000")


@pytest.fixture
def complex_nested_data(sample_uuid: uuid.UUID) -> dict[str, Any]:
    """生成包含多种类型的嵌套数据"""
    return {
        "meta": {
            "id": sample_uuid,
            "created_at": datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            "tags": {"a", "b", "c"},  # Set
        },
        "metrics": [
            Decimal("99.99"),
            Decimal("1.1234567890123456789"),  # High precision
            float("inf"),  # Infinity (if supported handling checks)
        ],
        "is_active": True,
        "none_val": None,
    }


# =========================================================
# 1. Unit Tests: JSON Toolkit (底层序列化逻辑)
# =========================================================


class TestOrjsonToolkit:
    """测试 pkg/toolkit/json.py 的核心序列化与反序列化能力"""

    @pytest.mark.parametrize(
        "input_data, expected_subset",
        [
            (2**53 + 1, 2**53 + 1),  # 大整数
            (42, 42),  # 普通整数
            (3.14159, 3.14159),  # 浮点数
            (True, True),  # 布尔值
            (None, None),  # None
            (
                {"a", "b"},
                ["a", "b"],
            ),  # Set -> List (无序，需特殊断言，此处仅作示例结构)
            (b"test_bytes", "test_bytes"),  # Bytes -> Str
        ],
    )
    def test_basic_type_round_trip(self, input_data: Any, expected_subset: Any):
        """验证基本类型的序列化和反序列化回路"""
        json_str = orjson_dumps({"val": input_data})
        parsed = orjson_loads(json_str)

        if isinstance(input_data, set):
            assert set(parsed["val"]) == input_data
        else:
            assert parsed["val"] == expected_subset

    @pytest.mark.parametrize(
        "decimal_val, expected_type, check_val",
        [
            (Decimal("999.99"), float, 999.99),
            (Decimal("0.0"), float, 0.0),
            (
                Decimal("0.12345678901234567890"),
                str,
                "0.12345678901234567890",
            ),  # 高精度 -> 字符串
            (Decimal("1E+20"), str, "1E+20"),  # 大范围 -> 字符串
        ],
    )
    def test_decimal_strategy(
        self, decimal_val: Decimal, expected_type: type, check_val: Any
    ):
        """验证 Decimal 的智能转换策略：安全范围内转 float，否则转 string 以防精度丢失"""
        res = orjson_loads(orjson_dumps({"d": decimal_val}))
        assert isinstance(res["d"], expected_type)
        if expected_type is str:
            # 字符串比较需考虑科学计数法格式化差异，这里做简单包含或相等检查
            assert str(check_val).lower() in res["d"].lower()
        else:
            assert res["d"] == check_val

    def test_datetime_handling(self):
        """验证时区和时间格式"""
        # UTC 时间
        dt_utc = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
        parsed = orjson_loads(orjson_dumps({"dt": dt_utc}))
        assert parsed["dt"].endswith("+00:00") or parsed["dt"].endswith("Z")

        # 无时区时间 (Naive)
        dt_naive = datetime(2023, 1, 1, 12, 0, 0)
        parsed_naive = orjson_loads(orjson_dumps({"dt": dt_naive}))
        assert "2023-01-01" in parsed_naive["dt"]

    def test_numpy_support(self):
        """验证 Numpy 类型支持 (如果环境存在)"""
        if not HAS_NUMPY:
            pytest.skip("Numpy not installed")

        data = {
            "arr": np.array([1, 2, 3]),
            "int64": np.int64(9223372036854775807),
            "float32": np.float32(1.5),
        }
        parsed = orjson_loads(orjson_dumps(data))
        assert parsed["arr"] == [1, 2, 3]
        assert parsed["int64"] == 9223372036854775807
        assert parsed["float32"] == 1.5

    def test_dumps_options(self):
        """测试 dumps 和 dumps_bytes 的返回类型"""
        data = {"k": "v"}
        assert isinstance(orjson_dumps(data), str)
        assert isinstance(orjson_dumps_bytes(data), bytes)

    def test_error_handling(self):
        """测试异常处理"""

        # 测试不可序列化的对象
        class Unserializable:
            pass

        with pytest.raises(ValueError, match="JSON Serialization Failed"):
            orjson_dumps({"obj": Unserializable()})

        # 测试无效的 JSON 字符串
        with pytest.raises(ValueError, match="JSON Deserialization Failed"):
            orjson_loads("{invalid_json}")


# =========================================================
# 2. Unit Tests: Response Wrappers (响应封装逻辑)
# =========================================================


class TestResponseWrappers:
    """测试 pkg/toolkit/response.py 的响应封装函数"""

    def test_success_response_structure(self):
        """验证成功响应的标准结构"""
        data = {"uid": 100}
        resp = success_response(data=data)

        assert isinstance(resp, ResponsePayload)
        body = resp.model_dump(mode="json")
        assert body == {"code": 20000, "message": "", "data": data}

    def test_success_list_response_structure(self):
        """验证列表分页响应的标准结构"""
        items = [{"id": 1}, {"id": 2}]
        resp = success_list_response(data=items, page=1, limit=10, total=50)

        assert isinstance(resp, ResponsePayload)
        body = resp.model_dump(mode="json")
        assert body["code"] == 20000
        assert body["data"] == {"items": items, "page": 1, "limit": 10, "total": 50}

    @pytest.mark.parametrize(
        "lang, message, expected_msg",
        [
            ("zh", None, "请求参数错误"),
            ("en", None, "Bad Request"),
            ("zh", "缺少ID", "请求参数错误: 缺少ID"),
            ("en", "Missing ID", "Bad Request: Missing ID"),
        ],
    )
    def test_error_response_rendering(self, lang, message, expected_msg):
        """验证错误响应的多语言和自定义消息拼接"""
        # 模拟 AppError 定义：40000 -> {zh: 请求参数错误, en: Bad Request}
        error = AppError(40000, {"zh": "请求参数错误", "en": "Bad Request"})

        resp = error_response(error, message=message, lang=lang)
        assert isinstance(resp, JSONResponse)
        body = orjson_loads(resp.body)

        assert body["code"] == 40000
        assert body["data"] is None
        assert body["message"] == expected_msg

    def test_sse_wrapper(self):
        """验证 SSE 数据格式化"""
        # 简单字符串
        assert wrap_sse_data("ping") == "data: ping\n\n"
        # 字典自动序列化
        assert wrap_sse_data({"a": 1}) == 'data: {"a":1}\n\n'
        # 中文不应被转义
        sse_msg = wrap_sse_data({"msg": "测试"})
        assert "测试" in sse_msg

    def test_sse_event_wrapper(self):
        """验证带事件名的 SSE 数据格式化"""
        assert wrap_sse_event("message", "ping") == "event: message\ndata: ping\n\n"
        assert wrap_sse_event("step", {"a": 1}) == 'event: step\ndata: {"a":1}\n\n'

    def test_pydantic_integration(self):
        """验证 Pydantic 模型直接作为响应数据"""
        user = UserSchema(id=1, name="Admin", meta={"role": "root"})
        resp = success_response(data=user)
        body = resp.model_dump(mode="json")

        assert body["data"]["id"] == 1
        assert body["data"]["meta"]["role"] == "root"


# =========================================================
# 3. Integration Tests: FastAPI + TestClient (端到端测试)
# =========================================================

# 定义测试用 FastAPI 应用
app_test = FastAPI()


@app_test.get("/api/types", response_model=BaseResponse[dict[str, Any]])
def endpoint_types() -> ResponsePayload:
    return success_response(
        {
            "big_int": 2**60,
            "decimal": Decimal("100.50"),
            "date": datetime(2025, 12, 25, 10, 0, 0),
            "uuid": uuid.UUID("550e8400-e29b-41d4-a716-446655440000"),
        }
    )


@app_test.get("/api/list", response_model=BaseListResponse[int])
def endpoint_list() -> ResponsePayload:
    return success_list_response([1, 2, 3], page=1, limit=10, total=100)


@app_test.get("/api/error")
def endpoint_error(custom_msg: str | None = None) -> JSONResponse:
    err = AppError(50001, {"zh": "系统繁忙", "en": "System Busy"})
    return error_response(err, message=custom_msg)


@app_test.get("/api/nan", response_model=BaseResponse[dict[str, Any]])
def endpoint_nan() -> ResponsePayload:
    return success_response({"val": float("nan")})


client = TestClient(app_test)


class TestFastAPIIntegration:
    """模拟前端真实请求，验证 HTTP 协议层面的表现"""

    def test_complex_serialization_over_http(self):
        """测试通过 HTTP 传输复杂类型"""
        resp = client.get("/api/types")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/json"

        data = resp.json()
        assert data["code"] == 20000
        # 验证大整数未丢失精度（Python client 自动处理，但在 JS 前端需注意）
        assert data["data"]["big_int"] == 2**60
        assert (
            data["data"]["decimal"] == "100.50"
        )  # FastAPI/Pydantic 默认保留 Decimal 文本精度
        assert "2025-12-25" in data["data"]["date"]
        assert data["data"]["uuid"] == "550e8400-e29b-41d4-a716-446655440000"

    def test_standard_response_fields(self):
        """验证所有接口都包含 code, message, data"""
        for path in ["/api/types", "/api/list"]:
            data = client.get(path).json()
            assert "code" in data
            assert "message" in data
            assert "data" in data

    def test_error_flow(self):
        """测试错误处理流程"""
        # 默认消息
        resp = client.get("/api/error")
        assert resp.json()["message"] == "系统繁忙"

        # 自定义消息追加
        resp = client.get("/api/error?custom_msg=DB_TIMEOUT")
        assert resp.json()["message"] == "系统繁忙: DB_TIMEOUT"

    def test_content_encoding(self):
        """验证包含 Unicode 字符的响应编码正确"""

        # 构造包含中文、Emoji 的响应
        @app_test.get("/api/unicode", response_model=BaseResponse[dict[str, str]])
        def endpoint_unicode() -> ResponsePayload:
            return success_response({"msg": "你好", "emoji": "🚀"})

        resp = client.get("/api/unicode")
        assert resp.encoding == "utf-8"  # TestClient 自动推断，但也验证了 header
        assert resp.json()["data"]["msg"] == "你好"
        assert resp.json()["data"]["emoji"] == "🚀"

    def test_handling_invalid_numbers(self):
        """测试 NaN/Infinity 的处理 (Robustness)"""
        resp = client.get("/api/nan")

        # 1. 确保服务正常响应
        assert resp.status_code == 200

        body = resp.json()
        val = body["data"]["val"]

        # 2. 严格断言：必须由 FastAPI/Pydantic 转换为 JSON null
        assert val is None, (
            f"Expected invalid number to be converted to None (JSON null), but got type: {type(val)} value: {val}"
        )


if __name__ == "__main__":
    # 配置日志输出，方便调试
    sys.exit(pytest.main(["-s", "-v", "--log-cli-level=INFO", __file__]))
