"""SQLite 轻量本地存储的真实 engine 与应用资源生命周期。"""

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError, OperationalError

from internal.config import SQLiteSettings
from internal.infra.database import connection as database_module
from pkg.database import new_async_engine, new_async_session_maker


@pytest.mark.asyncio
@pytest.mark.parametrize("memory", [True, False])
@pytest.mark.parametrize("explicit_transactions", [True, False])
async def test_sqlite_engine_executes_and_closes(
    tmp_path: Path,
    memory: bool,
    explicit_transactions: bool,
) -> None:
    uri = URL.create(
        "sqlite+aiosqlite",
        database=":memory:" if memory else str(tmp_path / "local.sqlite3"),
    ).render_as_string(hide_password=False)
    engine = new_async_engine(
        database_uri=uri,
        echo=False,
        sqlite_explicit_transactions=explicit_transactions,
    )
    try:
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT 1")) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_application_database_lifecycle_supports_sqlite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "application.sqlite3"
    initializer = new_async_engine(
        database_uri=URL.create("sqlite+aiosqlite", database=str(path)),
        echo=False,
    )
    async with initializer.connect():
        pass
    await initializer.dispose()
    monkeypatch.setattr(
        database_module,
        "settings",
        SimpleNamespace(
            debug=False,
            db=SimpleNamespace(
                echo=False,
                sqlite=SQLiteSettings(
                    busy_timeout_ms=2345,
                    allow_create=False,
                    explicit_transactions=True,
                ),
            ),
            sqlalchemy_database_uri=URL.create("sqlite+aiosqlite", database=str(path)),
            sqlalchemy_read_database_uri=None,
        ),
    )
    database_module.reset_async_db()
    database_module.init_async_db()
    try:
        async with database_module.get_session() as session:
            assert await session.scalar(text("SELECT 1")) == 1
            assert await session.scalar(text("PRAGMA busy_timeout")) == 2345
            await session.execute(text("CREATE TABLE rolled_back (id INTEGER)"))
            await session.rollback()
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM sqlite_master WHERE name='rolled_back'")
                )
                == 0
            )
        await database_module.check_database_revision()
    finally:
        await database_module.close_async_db()
    assert database_module._engine is None
    path.unlink()
    database_module.init_async_db()
    try:
        with pytest.raises(OperationalError):
            async with database_module.get_session() as session:
                await session.execute(text("SELECT 1"))
        assert not path.exists()
    finally:
        await database_module.close_async_db()


@pytest.mark.parametrize("backend", ["mysql", "oracle", "mssql"])
def test_engine_rejects_unsupported_backend_before_loading_driver(backend: str) -> None:
    with pytest.raises(ValueError, match="PostgreSQL and SQLite only") as exc:
        new_async_engine(
            database_uri=f"{backend}://user:test-password@example.invalid/db"
        )
    assert "test-password" not in str(exc.value)


@pytest.mark.parametrize("timeout", [True, False])
def test_sqlite_engine_rejects_boolean_busy_timeout(timeout: bool) -> None:
    with pytest.raises(ValueError, match="busy timeout"):
        new_async_engine(
            database_uri="sqlite+aiosqlite:///:memory:",
            sqlite_busy_timeout_ms=timeout,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("file_uri", [False, True])
@pytest.mark.parametrize(
    "uri", [None, "true", "True", "1", "yes", "false", "False", "0", "no"]
)
async def test_existing_file_policy_normalizes_path_and_uri_flag(
    tmp_path: Path,
    file_uri: bool,
    uri: str | None,
) -> None:
    path = tmp_path / "existing ?#% 数据.sqlite3"
    initializer = new_async_engine(
        database_uri=URL.create("sqlite+aiosqlite", database=str(path)),
        echo=False,
    )
    try:
        async with initializer.begin() as connection:
            await connection.execute(text("CREATE TABLE items (id INTEGER)"))
            await connection.execute(text("INSERT INTO items VALUES (42)"))
    finally:
        await initializer.dispose()
    engine = new_async_engine(
        database_uri=URL.create(
            "sqlite+aiosqlite",
            database=f"file:{quote(str(path), safe='/')}" if file_uri else str(path),
            query={} if uri is None else {"uri": uri},
        ),
        echo=False,
        sqlite_allow_create=False,
    )
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT id FROM items")) == 42
        await engine.dispose()
        path.unlink()
        with pytest.raises(OperationalError):
            async with engine.connect():
                pytest.fail("Missing file must not be recreated")
        assert list(tmp_path.iterdir()) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_rolls_back_ddl_and_released_savepoint() -> None:
    engine = new_async_engine(
        database_uri="sqlite+aiosqlite:///:memory:",
        echo=False,
        sqlite_explicit_transactions=True,
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text("CREATE TABLE rolled_back (id INTEGER)"))
            await connection.rollback()
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM sqlite_master WHERE name='rolled_back'")
                )
                == 0
            )
            await connection.rollback()

        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE items (id INTEGER)"))
        session_factory = new_async_session_maker(engine)
        async with session_factory() as session:
            async with session.begin_nested():
                await session.execute(text("INSERT INTO items VALUES (1)"))
            await session.rollback()
            assert await session.scalar(text("SELECT count(*) FROM items")) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_enforces_foreign_keys_on_each_connection(tmp_path: Path) -> None:
    engine = new_async_engine(
        database_uri=URL.create(
            "sqlite+aiosqlite",
            database=str(tmp_path / "fk.sqlite3"),
        ),
        echo=False,
        sqlite_busy_timeout_ms=1234,
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("CREATE TABLE parents (id INTEGER PRIMARY KEY)")
            )
            await connection.execute(
                text("CREATE TABLE children (parent_id INTEGER REFERENCES parents(id))")
            )
        async with engine.connect() as first, engine.connect() as second:
            for connection in (first, second):
                assert await connection.scalar(text("PRAGMA foreign_keys")) == 1
                assert await connection.scalar(text("PRAGMA busy_timeout")) == 1234
            with pytest.raises(IntegrityError):
                await second.execute(text("INSERT INTO children VALUES (999)"))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_existing_file_policy_survives_reconnection(
    tmp_path: Path,
) -> None:
    path = tmp_path / "测试 ?#% database.sqlite3"
    url = URL.create("sqlite+aiosqlite", database=str(path))
    engine = new_async_engine(database_uri=url, echo=False, sqlite_allow_create=False)
    try:
        with pytest.raises(OperationalError):
            async with engine.connect():
                pytest.fail("Missing file must not be created")
        assert not path.exists()

        initializer = new_async_engine(database_uri=url, echo=False)
        try:
            async with initializer.begin() as connection:
                await connection.execute(text("CREATE TABLE items (id INTEGER)"))
                await connection.execute(text("INSERT INTO items VALUES (1)"))
        finally:
            await initializer.dispose()
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT id FROM items")) == 1
        await engine.dispose()
        path.unlink()
        with pytest.raises(OperationalError):
            async with engine.connect():
                pytest.fail("Reconnection must not recreate a removed database")
        assert list(tmp_path.iterdir()) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_memory_ignores_existing_file_policy() -> None:
    engine = new_async_engine(
        database_uri="sqlite+aiosqlite:///:memory:",
        echo=False,
        sqlite_allow_create=False,
    )
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT 1")) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_existing_file_policy_preserves_read_only_uri(tmp_path: Path) -> None:
    path = tmp_path / "read only.sqlite3"
    initializer = new_async_engine(
        database_uri=URL.create("sqlite+aiosqlite", database=str(path)),
        echo=False,
    )
    try:
        async with initializer.begin() as connection:
            await connection.execute(text("CREATE TABLE items (id INTEGER)"))
    finally:
        await initializer.dispose()
    reader = new_async_engine(
        database_uri=URL.create(
            "sqlite+aiosqlite",
            database=f"file:{quote(str(path), safe='/')}",
            query={"uri": "true", "mode": "ro"},
        ),
        echo=False,
        sqlite_allow_create=False,
    )
    try:
        async with reader.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM items")) == 0
            with pytest.raises(OperationalError, match="readonly"):
                await connection.execute(text("INSERT INTO items VALUES (1)"))
    finally:
        await reader.dispose()
