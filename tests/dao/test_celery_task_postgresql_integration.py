import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from internal.dao.celery_task import CeleryTaskDao


def _integration_database_url() -> str:
    value = os.getenv("TEST_POSTGRESQL_URL")
    if not value:
        pytest.skip("TEST_POSTGRESQL_URL is required for PostgreSQL integration tests")
    return value


async def _create_test_schema(database_url: str, schema: str) -> None:
    ddl = Path("ddl/postgresql/1.2.0_celery_task_state_machine.sql").read_text(
        encoding="utf-8"
    )
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            await connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
            for statement in ddl.split(";"):
                if statement.strip():
                    await connection.exec_driver_sql(statement)
    finally:
        await engine.dispose()


async def _drop_test_schema(database_url: str, schema: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgresql_ddl_and_concurrent_claim() -> None:
    database_url = _integration_database_url()
    schema = f"celery_task_sm_{uuid4().hex}"
    await _create_test_schema(database_url, schema)

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_provider():
        async with session_factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            await session.commit()
            yield session

    try:
        async with session_factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            await session.execute(
                text(
                    """
                    INSERT INTO celery_task_record (
                        id, task_name, trace_id, scope, queue,
                        idempotency_key_hash, payload_hash, status,
                        attempt_count, created_at, updated_at
                    ) VALUES (
                        123, 'task.name', 'trace-1', 'user:1', 'celery_queue',
                        :idempotency_key_hash, :payload_hash, 'QUEUED',
                        0, :now, :now
                    )
                    """
                ),
                {
                    "idempotency_key_hash": "a" * 64,
                    "payload_hash": "b" * 64,
                    "now": datetime(2026, 7, 7, 8, 0, 0),
                },
            )
            await session.commit()

        dao = CeleryTaskDao(session_provider=session_provider)
        claims = await asyncio.gather(
            dao.claim_execution(
                record_id=123,
                task_name="task.name",
                scope="user:1",
                execution_token="delivery-a",
                hard_deadline_seconds=60,
            ),
            dao.claim_execution(
                record_id=123,
                task_name="task.name",
                scope="user:1",
                execution_token="delivery-b",
                hard_deadline_seconds=60,
            ),
        )

        assert sum(record is not None for record in claims) == 1
        async with session_factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            row = (
                await session.execute(
                    text(
                        """
                        SELECT status, execution_token, attempt_count,
                               started_at, hard_deadline_at
                        FROM celery_task_record
                        WHERE id = 123
                        """
                    )
                )
            ).one()
        assert row.status == "RUNNING"
        assert row.execution_token in {"delivery-a", "delivery-b"}
        assert row.attempt_count == 1
        assert row.started_at is not None
        assert row.hard_deadline_at > row.started_at
    finally:
        await engine.dispose()
        await _drop_test_schema(database_url, schema)
