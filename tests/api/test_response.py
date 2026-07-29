import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from internal.schemas import BaseListResponse, BaseResponse
from pkg.api_response import (
    AppError,
    ResponsePayload,
    error_response,
    success_list_response,
    success_response,
    wrap_sse_data,
    wrap_sse_event,
)
from pkg.toolkit.json import orjson_loads


class UserSchema(BaseModel):
    id: int
    name: str
    meta: dict[str, Any] = Field(default_factory=dict)


class TestResponseWrappers:
    """测试 pkg/api_response/__init__.py 的响应封装函数"""

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
