"""认证模块测试"""

import pytest
import pytest_asyncio
from uuid import UUID
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from internal.controllers.api import auth as auth_controller
from internal.core import AppException
from internal.dependencies.auth import (
    AuthenticatedPrincipal,
)
from internal.schemas.user import (
    UserLoginReqSchema,
    UserRegisterReqSchema,
    WeChatLoginReqSchema,
)
from internal.schemas.user import AuthUserDTO, UserLoginDTO
from internal.services.auth import new_auth_service

TEST_USER_ID = UUID("00000000-0000-7000-8000-000000000999")
LOGIN_USER_ID = UUID("00000000-0000-7000-8000-000000000001")
REGISTERED_USER_ID = UUID("00000000-0000-7000-8000-000000000002")
WECHAT_USER_ID = UUID("00000000-0000-7000-8000-000000000003")


class FakeAuthService:
    def __init__(self) -> None:
        self.verify_token_calls: list[str] = []

    async def verify_token(self, token: str) -> dict[str, str]:
        assert token in {"tk_logout", "tk_me"}
        self.verify_token_calls.append(token)
        return {"id": TEST_USER_ID.hex}

    async def login(self, *, username: str, password: str) -> UserLoginDTO:
        assert username == "testuser"
        assert password == "password123"
        return UserLoginDTO(
            user=AuthUserDTO(id=LOGIN_USER_ID, name="testuser", phone="13800138000"),
            token="tk_login",
        )

    async def register(
        self, *, username: str, phone: str, password: str
    ) -> UserLoginDTO:
        assert username == "newuser"
        assert phone == "13800138001"
        assert password == "password123"
        return UserLoginDTO(
            user=AuthUserDTO(id=REGISTERED_USER_ID, name="newuser", phone=phone),
            token="tk_register",
        )

    async def logout(self, *, user_id: UUID | None, token: str | None) -> None:
        assert user_id == TEST_USER_ID
        assert token == "tk_logout"

    async def get_current_user(self, *, user_id: UUID | None) -> AuthUserDTO:
        assert user_id == TEST_USER_ID
        return AuthUserDTO(id=TEST_USER_ID, name="current", phone="13800138002")

    async def wechat_login(self, *, code: str) -> UserLoginDTO:
        assert code == "wechat_code"
        return UserLoginDTO(
            user=AuthUserDTO(id=WECHAT_USER_ID, name="wechat", phone=""),
            token="tk_wechat",
        )


def _response_payload(response) -> dict:
    return response.model_dump(mode="json")


@pytest_asyncio.fixture
async def auth_client():
    app = FastAPI()
    service = FakeAuthService()
    app.include_router(auth_controller.anonymous_router, prefix="/v1")
    app.include_router(auth_controller.authenticated_router, prefix="/v1")
    app.dependency_overrides[new_auth_service] = lambda: service

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client, service

    app.dependency_overrides.clear()


class TestAuthEndpoints:
    """测试认证 API 端点"""

    @pytest.mark.asyncio
    async def test_login_success(self, auth_client):
        """测试登录成功"""
        client, _ = auth_client
        response = await client.post(
            "/v1/auth/login",
            json={"username": "testuser", "password": "password123"},
        )
        assert response.status_code == 200
        assert response.json()["data"]["token"] == "tk_login"

    @pytest.mark.asyncio
    async def test_get_current_user_authenticated(self, auth_client):
        """测试获取当前用户信息（已认证）"""
        client, service = auth_client
        response = await client.get(
            "/v1/auth/me",
            headers={"Authorization": "Bearer tk_me"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 20000
        assert "id" in data["data"]
        assert "name" in data["data"]
        assert service.verify_token_calls == ["tk_me"]

    @pytest.mark.asyncio
    async def test_authenticated_router_rejects_missing_token(self, auth_client):
        client, service = auth_client

        with pytest.raises(AppException):
            await client.get("/v1/auth/me")

        assert service.verify_token_calls == []

    @pytest.mark.asyncio
    async def test_logout(self, auth_client):
        """测试登出"""
        client, service = auth_client
        response = await client.post(
            "/v1/auth/logout", headers={"Authorization": "Bearer tk_logout"}
        )
        assert response.status_code == 200
        assert response.json()["code"] == 20000
        assert service.verify_token_calls == ["tk_logout"]


class TestAuthControllerResponseModels:
    """测试认证 Controller 将 Service 业务数据组装为响应模型。"""

    @pytest.mark.asyncio
    async def test_login_wraps_service_data_in_response_model(self):
        response = await auth_controller.login(
            UserLoginReqSchema(username="testuser", password="password123"),
            FakeAuthService(),
        )

        assert _response_payload(response) == {
            "code": 20000,
            "message": "",
            "data": {
                "user": {
                    "id": str(LOGIN_USER_ID),
                    "name": "testuser",
                    "phone": "13800138000",
                },
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

        assert _response_payload(response)["data"] == {
            "user": {
                "id": str(REGISTERED_USER_ID),
                "name": "newuser",
                "phone": "13800138001",
            },
            "token": "tk_register",
        }

    @pytest.mark.asyncio
    async def test_logout_uses_success_envelope(self):
        response = await auth_controller.logout(
            FakeAuthService(),
            AuthenticatedPrincipal(user_id=TEST_USER_ID, token="tk_logout"),
        )

        assert _response_payload(response) == {
            "code": 20000,
            "message": "",
            "data": {"message": "登出成功"},
        }

    @pytest.mark.asyncio
    async def test_get_current_user_wraps_service_data_in_response_model(self):
        response = await auth_controller.get_current_user(FakeAuthService())

        assert _response_payload(response)["data"] == {
            "id": str(TEST_USER_ID),
            "name": "current",
            "phone": "13800138002",
        }

    @pytest.mark.asyncio
    async def test_wechat_login_wraps_service_data_in_response_model(self):
        response = await auth_controller.wechat_login(
            WeChatLoginReqSchema(code="wechat_code"),
            FakeAuthService(),
        )

        assert _response_payload(response)["data"] == {
            "user": {"id": str(WECHAT_USER_ID), "name": "wechat", "phone": ""},
            "token": "tk_wechat",
        }
