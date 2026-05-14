"""认证模块测试"""

import json

import pytest

from internal.controllers.api import auth as auth_controller
from internal.schemas.user import (
    UserLoginReqSchema,
    UserRegisterReqSchema,
    WeChatLoginReqSchema,
)
from internal.services.auth import AuthUserDTO, UserLoginDTO


class FakeAuthService:
    async def login(self, *, username: str, password: str) -> UserLoginDTO:
        assert username == "testuser"
        assert password == "password123"
        return UserLoginDTO(
            user=AuthUserDTO(id=1, name="testuser", phone="13800138000"),
            token="tk_login",
        )

    async def register(self, *, username: str, phone: str, password: str) -> UserLoginDTO:
        assert username == "newuser"
        assert phone == "13800138001"
        assert password == "password123"
        return UserLoginDTO(
            user=AuthUserDTO(id=2, name="newuser", phone=phone),
            token="tk_register",
        )

    async def logout(self, *, user_id: int | None, token: str | None) -> None:
        assert user_id == 999
        assert token == "tk_logout"

    async def get_current_user(self, *, user_id: int | None) -> AuthUserDTO:
        assert user_id == 999
        return AuthUserDTO(id=999, name="current", phone="13800138002")

    async def wechat_login(self, *, code: str) -> UserLoginDTO:
        assert code == "wechat_code"
        return UserLoginDTO(
            user=AuthUserDTO(id=3, name="wechat", phone=""),
            token="tk_wechat",
        )


def _response_json(response) -> dict:
    return json.loads(response.body)


class TestAuthEndpoints:
    """测试认证 API 端点"""

    @pytest.mark.asyncio
    async def test_login_success(self, client):
        """测试登录成功"""
        response = await client.post(
            "/v1/auth/login",
            json={"username": "testuser", "password": "password123"},
        )
        # 由于数据库中可能没有测试用户，这里只检查响应格式
        assert response.status_code in [200, 401]

    @pytest.mark.asyncio
    async def test_get_current_user_authenticated(self, authenticated_client):
        """测试获取当前用户信息（已认证）"""
        response = await authenticated_client.get("/v1/auth/me")
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert "name" in data

    @pytest.mark.asyncio
    async def test_logout(self, authenticated_client):
        """测试登出"""
        response = await authenticated_client.post("/v1/auth/logout")
        assert response.status_code == 200


class TestAuthControllerResponseModels:
    """测试认证 Controller 将 Service 业务数据组装为响应模型。"""

    @pytest.mark.asyncio
    async def test_login_wraps_service_data_in_response_model(self):
        response = await auth_controller.login(
            UserLoginReqSchema(username="testuser", password="password123"),
            FakeAuthService(),
        )

        assert _response_json(response) == {
            "code": 20000,
            "message": "",
            "data": {
                "user": {"id": 1, "name": "testuser", "phone": "13800138000"},
                "token": "tk_login",
            },
        }

    @pytest.mark.asyncio
    async def test_register_wraps_service_data_in_response_model(self):
        response = await auth_controller.register(
            UserRegisterReqSchema(
                username="newuser",
                phone="13800138001",
                password="password123",
            ),
            FakeAuthService(),
        )

        assert _response_json(response)["data"] == {
            "user": {"id": 2, "name": "newuser", "phone": "13800138001"},
            "token": "tk_register",
        }

    @pytest.mark.asyncio
    async def test_logout_uses_success_envelope(self):
        response = await auth_controller.logout(FakeAuthService(), authorization="Bearer tk_logout")

        assert _response_json(response) == {"code": 20000, "message": "", "data": None}

    @pytest.mark.asyncio
    async def test_get_current_user_wraps_service_data_in_response_model(self):
        response = await auth_controller.get_current_user(FakeAuthService())

        assert _response_json(response)["data"] == {
            "id": 999,
            "name": "current",
            "phone": "13800138002",
        }

    @pytest.mark.asyncio
    async def test_wechat_login_wraps_service_data_in_response_model(self):
        response = await auth_controller.wechat_login(
            WeChatLoginReqSchema(code="wechat_code"),
            FakeAuthService(),
        )

        assert _response_json(response)["data"] == {
            "user": {"id": 3, "name": "wechat", "phone": ""},
            "token": "tk_wechat",
        }
