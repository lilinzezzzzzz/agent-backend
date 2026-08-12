from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt as pyjwt
import pytest

from pkg.security import JWTHandler

TEST_USER_ID = UUID("00000000-0000-7000-8000-000000000123")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def jwt_handler():
    return JWTHandler(secret="test_secret_key", algorithm="HS256", expire_minutes=30)


@pytest.mark.anyio
class TestJWTHandler:
    def test_create_token_success(self, jwt_handler):
        token = jwt_handler.create_token(user_id=TEST_USER_ID, username="test_user")

        assert token is not None
        assert isinstance(token, str)

        payload = pyjwt.decode(token, "test_secret_key", algorithms=["HS256"])
        assert payload["user_id"] == TEST_USER_ID.hex
        assert payload["username"] == "test_user"
        assert "exp" in payload

    def test_create_token_custom_expire(self, jwt_handler):
        token = jwt_handler.create_token(
            user_id=TEST_USER_ID, username="test", expire_minutes=60
        )

        payload = pyjwt.decode(token, "test_secret_key", algorithms=["HS256"])
        exp_time = datetime.fromtimestamp(payload["exp"], tz=UTC)
        now = datetime.now(UTC)

        diff = exp_time - now
        assert 59 <= diff.total_seconds() / 60 <= 61

    async def test_verify_token_success(self, jwt_handler):
        token = jwt_handler.create_token(user_id=TEST_USER_ID, username="test_user")
        bearer_token = f"Bearer {token}"

        user_id, is_valid = jwt_handler.verify_token(bearer_token)

        assert is_valid is True
        assert user_id == TEST_USER_ID

    async def test_verify_token_no_bearer_prefix(self, jwt_handler):
        token = jwt_handler.create_token(user_id=TEST_USER_ID, username="test")

        user_id, is_valid = jwt_handler.verify_token(token)

        assert is_valid is False
        assert user_id is None

    async def test_verify_token_empty(self, jwt_handler):
        user_id, is_valid = jwt_handler.verify_token("")

        assert is_valid is False
        assert user_id is None

    async def test_verify_token_none(self, jwt_handler):
        user_id, is_valid = jwt_handler.verify_token(None)

        assert is_valid is False
        assert user_id is None

    async def test_verify_token_expired(self, jwt_handler):
        expired_payload = {
            "user_id": 1,
            "username": "test",
            "exp": int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
        }
        expired_token = pyjwt.encode(
            expired_payload, "test_secret_key", algorithm="HS256"
        )
        bearer_token = f"Bearer {expired_token}"

        user_id, is_valid = jwt_handler.verify_token(bearer_token)

        assert is_valid is False
        assert user_id is None

    async def test_verify_token_invalid_secret(self, jwt_handler):
        wrong_token = pyjwt.encode(
            {
                "user_id": 1,
                "username": "test",
                "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            },
            "wrong_secret",
            algorithm="HS256",
        )
        bearer_token = f"Bearer {wrong_token}"

        user_id, is_valid = jwt_handler.verify_token(bearer_token)

        assert is_valid is False
        assert user_id is None

    async def test_verify_token_missing_user_id(self, jwt_handler):
        payload = {
            "username": "test",
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        }
        token = pyjwt.encode(payload, "test_secret_key", algorithm="HS256")
        bearer_token = f"Bearer {token}"

        user_id, is_valid = jwt_handler.verify_token(bearer_token)

        assert is_valid is False
        assert user_id is None

    async def test_verify_token_invalid_format(self, jwt_handler):
        user_id, is_valid = jwt_handler.verify_token("Bearer invalid.token.here")

        assert is_valid is False
        assert user_id is None
