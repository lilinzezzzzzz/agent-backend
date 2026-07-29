import re
from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from internal.models.agent_audit import AgentAudit
from internal.models.agent_conversation import (
    AgentMessage,
    AgentRun,
    AgentRunStep,
    AgentSession,
)
from internal.models.celery_task import CeleryTaskRecord
from internal.models.scoped_operation_lock import ScopedOperationLock
from internal.models.third_party_account import ThirdPartyAccount
from internal.models.user import User


DDL_PATH = Path("ddl/postgresql/init.sql")
MODEL_TABLES = tuple(
    sorted(
        (
            AgentAudit.__table__,
            AgentMessage.__table__,
            AgentRun.__table__,
            AgentRunStep.__table__,
            AgentSession.__table__,
            CeleryTaskRecord.__table__,
            ScopedOperationLock.__table__,
            ThirdPartyAccount.__table__,
            User.__table__,
        ),
        key=lambda table: table.name,
    )
)


def _normalize_sql(value: str) -> str:
    return " ".join(value.split())


def test_postgresql_init_ddl_matches_registered_model_metadata() -> None:
    ddl = DDL_PATH.read_text(encoding="utf-8")
    normalized_ddl = _normalize_sql(ddl)
    dialect = postgresql.dialect()

    for table in MODEL_TABLES:
        expected_table = _normalize_sql(
            str(CreateTable(table).compile(dialect=dialect))
        )
        assert expected_table in normalized_ddl, table.name

        for index in table.indexes:
            expected_index = _normalize_sql(
                str(CreateIndex(index).compile(dialect=dialect))
            )
            assert expected_index in normalized_ddl, index.name


def test_postgresql_init_ddl_contains_exactly_the_registered_model_tables() -> None:
    ddl = DDL_PATH.read_text(encoding="utf-8")
    ddl_tables = {
        quoted_name or plain_name
        for quoted_name, plain_name in re.findall(
            r'CREATE TABLE\s+(?:"([^"]+)"|([a-zA-Z_][a-zA-Z0-9_]*))\s*\(',
            ddl,
        )
    }

    assert ddl_tables == {table.name for table in MODEL_TABLES}
    assert "BIGSERIAL" not in ddl
    assert "ALTER TABLE" not in ddl
    assert "DROP TABLE" not in ddl
