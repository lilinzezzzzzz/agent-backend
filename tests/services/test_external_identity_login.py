from contextlib import asynccontextmanager
from datetime import datetime

import pytest
import pytest_asyncio

from internal.dao.external_identity import ExternalIdentityDao
from internal.dao.user import UserDao
from internal.services.user import UserService

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def _unexpected_read_session(autoflush: bool = True):
    del autoflush
    raise AssertionError("认证身份查询不应使用只读副本")
    yield


@pytest_asyncio.fixture
async def external_identity_service(db_session):
    @asynccontextmanager
    async def session_provider(autoflush: bool = True):
        async with db_session() as session:
            if autoflush:
                yield session
            else:
                with session.no_autoflush:
                    yield session

    user_dao = UserDao(
        session_provider=session_provider,
        read_session_provider=session_provider,
    )
    identity_dao = ExternalIdentityDao(
        session_provider=session_provider,
        read_session_provider=_unexpected_read_session,
    )
    return UserService(user_dao, identity_dao), user_dao, identity_dao


async def test_external_identity_login_persists_and_reuses_mapping(
    external_identity_service,
    monkeypatch,
) -> None:
    service, user_dao, identity_dao = external_identity_service
    first_authenticated_at = datetime(2026, 8, 5, 1, 2, 3)
    second_authenticated_at = datetime(2026, 8, 5, 2, 3, 4)
    authentication_times = iter((first_authenticated_at, second_authenticated_at))
    monkeypatch.setattr(
        "internal.services.user.utc_now_naive",
        lambda: next(authentication_times),
    )

    first_user = await service.get_or_create_user_by_external_identity(
        provider="wechat",
        connection_key="wechat_default",
        subject="case-sensitive-openid",
        email="first@example.com",
        union_id="union-1",
        nickname="first nickname",
        avatar="https://example.com/first.png",
    )

    stored_user = await user_dao.query_by_primary_id(first_user.id)
    identity = await identity_dao.get_by_identity(
        provider="wechat",
        connection_key="wechat_default",
        subject="case-sensitive-openid",
    )
    assert stored_user is not None
    assert identity is not None
    assert identity.user_id == first_user.id
    assert identity.last_authenticated_at == first_authenticated_at
    assert identity.email_snapshot == "first@example.com"
    assert identity.nickname_snapshot == "first nickname"
    assert not hasattr(identity, "access_token")
    assert not hasattr(identity, "refresh_token")

    second_user = await service.get_or_create_user_by_external_identity(
        provider="wechat",
        connection_key="wechat_default",
        subject="case-sensitive-openid",
        email="latest@example.com",
        nickname="latest nickname",
    )

    refreshed_identity = await identity_dao.get_by_identity(
        provider="wechat",
        connection_key="wechat_default",
        subject="case-sensitive-openid",
    )
    assert second_user.id == first_user.id
    assert refreshed_identity is not None
    assert refreshed_identity.id == identity.id
    assert refreshed_identity.last_authenticated_at == second_authenticated_at
    assert refreshed_identity.email_snapshot == "latest@example.com"
    assert refreshed_identity.nickname_snapshot == "latest nickname"
    assert refreshed_identity.union_id == "union-1"
    assert refreshed_identity.avatar_snapshot == "https://example.com/first.png"


async def test_external_identity_login_restores_soft_deleted_mapping(
    external_identity_service,
) -> None:
    service, user_dao, identity_dao = external_identity_service
    user = await service.get_or_create_user_by_external_identity(
        provider="wechat",
        connection_key="wechat_default",
        subject="rebind-openid",
    )
    identity = await identity_dao.get_by_identity(
        provider="wechat",
        connection_key="wechat_default",
        subject="rebind-openid",
    )
    assert identity is not None
    await identity_dao.soft_delete(identity)

    restored_user = await service.get_or_create_user_by_external_identity(
        provider="wechat",
        connection_key="wechat_default",
        subject="rebind-openid",
    )

    restored_identity = await identity_dao.get_by_identity(
        provider="wechat",
        connection_key="wechat_default",
        subject="rebind-openid",
    )
    async with user_dao.session_provider() as session:
        user_count = (await session.execute(user_dao.count_stmt())).scalar_one()
    assert restored_user.id == user.id
    assert restored_identity is not None
    assert restored_identity.id == identity.id
    assert user_count == 1


async def test_external_identity_connection_key_scopes_subject(
    external_identity_service,
) -> None:
    service, _, _ = external_identity_service

    first_user = await service.get_or_create_user_by_external_identity(
        provider="wechat",
        connection_key="wechat_app_a",
        subject="same-subject-text",
    )
    second_user = await service.get_or_create_user_by_external_identity(
        provider="wechat",
        connection_key="wechat_app_b",
        subject="same-subject-text",
    )

    assert first_user.id != second_user.id


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("provider", ""),
        ("provider", "p" * 33),
        ("connection_key", "c" * 65),
        ("subject", "s" * 257),
    ),
)
async def test_external_identity_login_rejects_invalid_identity_key(
    external_identity_service,
    field: str,
    value: str,
) -> None:
    service, _, _ = external_identity_service
    values = {
        "provider": "wechat",
        "connection_key": "wechat_default",
        "subject": "openid",
    }
    values[field] = value

    with pytest.raises(ValueError, match=field):
        await service.get_or_create_user_by_external_identity(**values)
