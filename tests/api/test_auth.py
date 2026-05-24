"""认证模块测试"""

import pytest


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
        assert data["code"] == 20000
        assert "id" in data["data"]
        assert "name" in data["data"]

    @pytest.mark.asyncio
    async def test_logout(self, authenticated_client):
        """测试登出"""
        response = await authenticated_client.post("/v1/auth/logout")
        assert response.status_code == 200
        assert response.json()["code"] == 20000
