import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.database.base import ModelMixin


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one(self) -> object:
        return self._value


class _FakeSession:
    def __init__(self, value: object) -> None:
        self.value = value

    async def execute(self, _statement: object) -> _ScalarResult:
        return _ScalarResult(self.value)


class _SQLiteSession:
    async def execute(self, statement: object) -> _ScalarResult:
        compiled = str(statement.compile(dialect=sqlite.dialect()))  # type: ignore[attr-defined]
        assert "CURRENT_TIMESTAMP" in compiled
        with sqlite3.connect(":memory:") as connection:
            value = connection.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
        return _ScalarResult(datetime.fromisoformat(value))


@pytest.mark.asyncio
async def test_get_database_now_preserves_naive_utc_value() -> None:
    expected = datetime(2026, 7, 7, 8, 9, 10)

    actual = await ModelMixin.get_database_now(
        cast(AsyncSession, _FakeSession(expected))
    )

    assert actual == expected
    assert actual.tzinfo is None


@pytest.mark.asyncio
async def test_get_database_now_converts_aware_value_to_naive_utc() -> None:
    database_value = datetime(
        2026,
        7,
        7,
        16,
        9,
        10,
        tzinfo=timezone(timedelta(hours=8)),
    )

    actual = await ModelMixin.get_database_now(
        cast(AsyncSession, _FakeSession(database_value))
    )

    assert actual == datetime(2026, 7, 7, 8, 9, 10)
    assert actual.tzinfo is None


@pytest.mark.asyncio
async def test_get_database_now_rejects_non_datetime_value() -> None:
    with pytest.raises(RuntimeError, match="database current time is not a datetime"):
        await ModelMixin.get_database_now(cast(AsyncSession, _FakeSession("now")))


@pytest.mark.asyncio
async def test_get_database_now_executes_on_sqlite() -> None:
    actual = await ModelMixin.get_database_now(cast(AsyncSession, _SQLiteSession()))

    assert isinstance(actual, datetime)
    assert actual.tzinfo is None
    assert abs(datetime.now(UTC).replace(tzinfo=None) - actual) < timedelta(seconds=5)
