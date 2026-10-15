import asyncio
from threading import Event, get_ident
from unittest.mock import AsyncMock, MagicMock

import pytest

from internal.services.user import UserService
from internal.utils.password import PasswordHandler


@pytest.fixture
def user_dao() -> MagicMock:
    dao = MagicMock()
    dao.is_phone_exist = AsyncMock(return_value=False)
    dao.insert = AsyncMock()
    return dao


@pytest.fixture
def user_service(user_dao: MagicMock) -> UserService:
    return UserService(dao=user_dao, external_identity_dao=MagicMock())


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["hash", "verify"])
async def test_password_work_offloads_without_blocking_event_loop(
    operation: str,
    user_service: UserService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    event_loop_thread = get_ident()

    def check_event_loop_progress() -> None:
        assert get_ident() != event_loop_thread
        released_by_event_loop = Event()
        loop.call_soon_threadsafe(released_by_event_loop.set)
        assert released_by_event_loop.wait(timeout=2)

    if operation == "hash":

        def hash_password(password: str) -> str:
            assert password == "test-password"
            check_event_loop_progress()
            return "stored-hash"

        monkeypatch.setattr(PasswordHandler, "hash_password", hash_password)
        await user_service.create_user(
            username="test-user",
            account="test-account",
            phone="13800138000",
            password="test-password",
        )
    else:

        def verify_password(password: str, password_hash: str) -> bool:
            assert password == "test-password"
            assert password_hash == "stored-hash"
            check_event_loop_progress()
            return True

        monkeypatch.setattr(PasswordHandler, "verify_password", verify_password)
        user = MagicMock(password_hash="stored-hash")
        assert await user_service.verify_password(user, "test-password")


@pytest.mark.asyncio
@pytest.mark.parametrize("password", ["test-password-中文", ""])
async def test_registration_hash_preserves_bcrypt_rules_and_verification(
    password: str,
    user_service: UserService,
    user_dao: MagicMock,
) -> None:
    await user_service.create_user(
        username="test-user",
        account="test-account",
        phone="13800138000",
        password=password,
    )
    stored_hash = user_dao.create.call_args.kwargs["password_hash"]
    assert stored_hash.startswith("$2b$12$")
    user = MagicMock(password_hash=stored_hash)
    assert await user_service.verify_password(user, password)
    assert not await user_service.verify_password(user, "wrong-password")
    assert not await user_service.verify_password(user, "x" * 73)
    user_dao.insert.assert_awaited_once_with(user_dao.create.return_value)


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_hash", [None, "", "not-a-bcrypt-hash"])
async def test_missing_or_invalid_password_hash_is_rejected(
    stored_hash: str | None,
    user_service: UserService,
) -> None:
    user = MagicMock(password_hash=stored_hash)
    assert not await user_service.verify_password(user, "test-password")


@pytest.mark.asyncio
async def test_registration_hash_error_prevents_persistence(
    user_service: UserService,
    user_dao: MagicMock,
) -> None:
    with pytest.raises(ValueError):
        await user_service.create_user(
            username="test-user",
            account="test-account",
            phone="13800138000",
            password="x" * 73,
        )
    user_dao.create.assert_not_called()
    user_dao.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_phone_is_rejected_before_hashing(
    user_service: UserService,
    user_dao: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_dao.is_phone_exist.return_value = True
    hash_password = MagicMock()
    monkeypatch.setattr(PasswordHandler, "hash_password", hash_password)
    with pytest.raises(ValueError, match="已被注册"):
        await user_service.create_user(
            username="test-user",
            account="test-account",
            phone="13800138000",
            password="test-password",
        )
    hash_password.assert_not_called()
    user_dao.create.assert_not_called()
    user_dao.insert.assert_not_awaited()
