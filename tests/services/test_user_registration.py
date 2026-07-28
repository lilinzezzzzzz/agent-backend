from unittest.mock import AsyncMock, MagicMock

import pytest

from internal.services.user import UserService
from pkg.database.base import AuditActor


@pytest.mark.asyncio
async def test_create_user_persists_constructed_model(monkeypatch) -> None:
    user = MagicMock()
    user_dao = MagicMock()
    user_dao.is_phone_exist = AsyncMock(return_value=False)
    user_dao.create.return_value = user
    user_dao.insert = AsyncMock()
    monkeypatch.setattr(
        "internal.services.user.PasswordHandler.hash_password", lambda _: "hashed"
    )
    service = UserService(dao=user_dao, third_party_dao=MagicMock())

    result = await service.create_user(
        username="new-user",
        account="13800138000",
        phone="13800138000",
        password="password123",
    )

    assert result is user
    user_dao.create.assert_called_once_with(
        audit_actor=AuditActor.system(),
        username="new-user",
        account="13800138000",
        phone="13800138000",
        password_hash="hashed",
    )
    user_dao.insert.assert_awaited_once_with(user)
