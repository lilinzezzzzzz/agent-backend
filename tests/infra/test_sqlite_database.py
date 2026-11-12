"""SQLite 轻量本地存储的真实 engine 与应用资源生命周期。"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL

from internal.infra.database import connection as database_module
from pkg.database import new_async_engine


@pytest.mark.asyncio
@pytest.mark.parametrize("memory", [True, False])
async def test_sqlite_engine_executes_and_closes(tmp_path: Path, memory: bool) -> None:
    uri = URL.create(
        "sqlite+aiosqlite",
        database=":memory:" if memory else str(tmp_path / "local.sqlite3"),
    ).render_as_string(hide_password=False)
    engine = new_async_engine(database_uri=uri, echo=False)
    try:
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT 1")) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_application_database_lifecycle_supports_sqlite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        database_module,
        "settings",
        SimpleNamespace(
            debug=False,
            db=SimpleNamespace(echo=False),
            sqlalchemy_database_uri="sqlite+aiosqlite:///:memory:",
            sqlalchemy_read_database_uri=None,
        ),
    )
    database_module.reset_async_db()
    database_module.init_async_db()
    try:
        async with database_module.get_session() as session:
            assert await session.scalar(text("SELECT 1")) == 1
    finally:
        await database_module.close_async_db()
    assert database_module._engine is None


@pytest.mark.parametrize("backend", ["mysql", "oracle", "mssql"])
def test_engine_rejects_unsupported_backend_before_loading_driver(backend: str) -> None:
    with pytest.raises(ValueError, match="PostgreSQL and SQLite only") as exc:
        new_async_engine(
            database_uri=f"{backend}://user:test-password@example.invalid/db"
        )
    assert "test-password" not in str(exc.value)
