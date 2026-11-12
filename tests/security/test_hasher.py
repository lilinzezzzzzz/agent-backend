"""统一 bcrypt 实现的策略、边界和历史哈希兼容性。"""

import bcrypt
import pytest

from pkg.security import Hasher, hash_password, verify_password


@pytest.mark.asyncio
async def test_allow_empty_policy_preserves_existing_service_behavior() -> None:
    compatible = Hasher(rounds=4, allow_empty=True)
    stored_hash = await compatible.hash("")

    assert await compatible.verify("", stored_hash)
    assert not await compatible.verify("wrong", stored_hash)
    assert not await Hasher(rounds=4).verify("", stored_hash)
    assert not await compatible.verify("", "invalid-hash")
    assert not await compatible.verify("", "")


@pytest.mark.asyncio
async def test_public_helpers_keep_rejecting_empty_passwords() -> None:
    with pytest.raises(ValueError, match="Password cannot be empty"):
        await hash_password("")
    assert not await verify_password("", "invalid-hash")


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["2a", "2b"])
async def test_existing_bcrypt_hashes_remain_valid(prefix: str) -> None:
    password = "existing-password-中文"
    stored_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=4))
    stored_hash = stored_hash.decode("utf-8").replace("$2b$", f"${prefix}$", 1)
    hasher = Hasher(rounds=12)

    assert await hasher.verify(password, stored_hash)
    assert not await hasher.verify("wrong-password", stored_hash)


@pytest.mark.asyncio
async def test_bcrypt_limit_counts_utf8_bytes_without_truncation() -> None:
    hasher = Hasher(rounds=4)
    password = "汉" * 24
    stored_hash = await hasher.hash(password)

    assert await hasher.verify(password, stored_hash)
    assert not await hasher.verify(password + "汉", stored_hash)
    with pytest.raises(ValueError):
        await hasher.hash(password + "汉")


@pytest.mark.parametrize(
    ("stored_hash", "rounds", "expected"),
    [
        ("$2b$12$payload", 12, False),
        ("$2b$14$payload", 12, False),
        ("$2b$10$payload", 12, True),
        ("$2b$04$payload", 4, False),
        ("$2b$04$payload", 6, True),
        ("$2a$12$payload", 12, True),
        ("invalid-hash", 12, True),
        ("$2b$invalid$payload", 12, True),
        (None, 12, True),
    ],
)
def test_needs_rehash_uses_the_configured_policy(
    stored_hash: str | None,
    rounds: int,
    expected: bool,
) -> None:
    assert Hasher(rounds=rounds).needs_rehash(stored_hash) is expected
