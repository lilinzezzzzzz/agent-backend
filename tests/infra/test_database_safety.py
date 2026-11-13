"""SQL 参数保护和数据库版本检查，不连接外部服务。"""

import logging
from types import SimpleNamespace

import pytest
from alembic.script import ScriptDirectory
from loguru import logger
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from internal.infra.database import connection as database_module
from pkg.database import new_async_engine


@pytest.mark.asyncio
@pytest.mark.parametrize("slow", [False, True])
async def test_sql_logs_hide_bound_values_and_keep_timing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    slow: bool,
) -> None:
    monkeypatch.setattr(
        database_module,
        "settings",
        SimpleNamespace(debug=not slow, SLOW_SQL_THRESHOLD=0.0 if slow else 0.5),
    )
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)))
    caplog.set_level(logging.INFO, logger="sqlalchemy.engine")
    engine = new_async_engine(database_uri="sqlite+aiosqlite:///:memory:", echo=True)
    database_module._register_event_listeners(engine)
    secret = "synthetic-private-value-7821"
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE items (value TEXT UNIQUE)"))
            await connection.execute(
                text("INSERT INTO items VALUES (:value)"),
                {"value": secret},
            )
        with pytest.raises(IntegrityError) as exc:
            async with engine.begin() as connection:
                await connection.execute(
                    text("INSERT INTO items VALUES (:value)"),
                    {"value": secret},
                )
        assert secret not in str(exc.value)
    finally:
        await engine.dispose()
        logger.remove(sink)

    output = "\n".join(messages)
    assert secret not in output
    assert ("SLOW SQL (" if slow else "SQL (") in output
    assert "INSERT INTO items VALUES (?)" in output
    assert secret not in caplog.text
    assert "SQL parameters hidden" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "revisions",
    [None, [], ["outdated"], ["unknown-future"], ["head", "extra"], ["head"]],
)
async def test_revision_check_rejects_unversioned_or_mismatched_database(
    revisions: list[str] | None,
) -> None:
    engine = new_async_engine(database_uri="sqlite+aiosqlite:///:memory:", echo=False)
    expected = ScriptDirectory(
        str(database_module.BASE_DIR / "migrations"),
    ).get_current_head()
    assert expected is not None
    try:
        async with engine.begin() as connection:
            if revisions is not None:
                await connection.execute(
                    text("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)"),
                )
                for revision in revisions:
                    await connection.execute(
                        text("INSERT INTO alembic_version VALUES (:revision)"),
                        {"revision": expected if revision == "head" else revision},
                    )
            if revisions == ["head"]:
                await connection.run_sync(database_module._check_database_revision)
            else:
                with pytest.raises(RuntimeError, match="alembic upgrade head"):
                    await connection.run_sync(database_module._check_database_revision)
            # 检查只读，不为未初始化的数据库创建版本表。
            if revisions is None:
                assert (
                    await connection.scalar(
                        text(
                            "SELECT count(*) FROM sqlite_master WHERE name='alembic_version'",
                        ),
                    )
                    == 0
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_revision_check_requires_initialized_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(database_module, "_engine", None)
    with pytest.raises(RuntimeError, match="not initialized"):
        await database_module.check_database_revision()
