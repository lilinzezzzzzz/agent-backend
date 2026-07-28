"""Scoped operation table lock 单元测试。"""

from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from internal.core import AppException
from internal.dao.scoped_operation_lock import ScopedOperationLockDao
from internal.models.scoped_operation_lock import ScopedOperationLock
from internal.services.scoped_operation_lock import LockMode, ScopedOperationLockService
from pkg.database.base import AuditActor, AuditActorType


class FakeDbapiError(Exception):
    """模拟 DBAPI 异常，提供 MySQL error code。"""


class FakeResult:
    """模拟 SQLAlchemy Result。"""

    def __init__(self, scalar_value: Any):
        self._scalar_value = scalar_value

    def scalar_one_or_none(self):
        return self._scalar_value


class FakeSession:
    """提供 ScopedOperationLockDao 所需的最小 session 行为。"""

    def __init__(
        self,
        *,
        dialect,
        in_transaction: bool = True,
        lock_row: ScopedOperationLock | None = None,
        select_error: OperationalError | None = None,
    ):
        self._in_transaction = in_transaction
        self._bind = MagicMock(dialect=dialect)
        self._lock_row = lock_row or ScopedOperationLock(
            id=1,
            operation_scope="scope",
            resource_key="key",
        )
        self._select_error = select_error
        self.executions: list[tuple[Any, dict[str, Any] | None]] = []

    def in_transaction(self) -> bool:
        return self._in_transaction

    def get_bind(self):
        return self._bind

    async def execute(self, statement, parameters=None):
        self.executions.append((statement, parameters))
        if parameters is None and self._select_error is not None:
            raise self._select_error
        return FakeResult(self._lock_row)


@pytest.fixture
def lock_dao() -> ScopedOperationLockDao:
    return ScopedOperationLockDao(session_provider=MagicMock())


@pytest.fixture
def lock_service(lock_dao: ScopedOperationLockDao) -> ScopedOperationLockService:
    return ScopedOperationLockService(dao=lock_dao)


def _mysql_lock_error(code: int) -> OperationalError:
    orig = FakeDbapiError()
    orig.args = (code, "lock unavailable")
    return OperationalError("SELECT ... FOR UPDATE NOWAIT", {}, orig)


class TestScopedOperationLockModel:
    def test_model_fields_match_lock_identity(self):
        assert ScopedOperationLock.__tablename__ == "scoped_operation_locks"
        assert ScopedOperationLock.operation_scope.property.columns[0].type.length == 64
        assert ScopedOperationLock.resource_key.property.columns[0].type.length == 128
        assert ScopedOperationLock.creator_id.property.columns[0].nullable is True
        assert ScopedOperationLock.creator_type.property.columns[0].nullable is False

    def test_model_has_unique_lock_identity(self):
        constraints = {
            constraint.name: constraint
            for constraint in ScopedOperationLock.__table__.constraints
            if constraint.name
        }

        constraint = constraints["uk_scoped_op_lock_key"]
        assert {column.name for column in constraint.columns} == {
            "operation_scope",
            "resource_key",
        }

    def test_model_disables_soft_delete_semantics(self):
        lock = ScopedOperationLock(operation_scope="scope", resource_key="key")

        assert ScopedOperationLock.has_deleted_at_column() is False
        assert lock.prepare_soft_delete() is None
        assert lock.prepare_restore() is None


class TestScopedOperationLockDao:
    @pytest.mark.asyncio
    async def test_wait_mode_ensures_row_then_locks_for_update(
        self,
        lock_dao: ScopedOperationLockDao,
    ):
        session = FakeSession(dialect=mysql.dialect())

        acquired = await lock_dao.acquire(
            session=cast(AsyncSession, session),
            operation_scope="scope",
            resource_key="key",
            wait=True,
            audit_actor=AuditActor.user(123),
        )

        insert_stmt, insert_params = session.executions[0]
        select_stmt, select_params = session.executions[1]
        compiled_select = str(select_stmt.compile(dialect=mysql.dialect()))

        assert acquired is True
        assert "INSERT IGNORE INTO scoped_operation_locks" in insert_stmt.text
        assert insert_params is not None
        assert insert_params["operation_scope"] == "scope"
        assert insert_params["resource_key"] == "key"
        assert insert_params["creator_id"] == 123
        assert insert_params["creator_type"] == AuditActorType.USER.value
        assert "FOR UPDATE" in compiled_select
        assert "NOWAIT" not in compiled_select
        assert select_params is None

    @pytest.mark.asyncio
    async def test_try_mode_uses_for_update_nowait(
        self, lock_dao: ScopedOperationLockDao
    ):
        session = FakeSession(dialect=mysql.dialect())

        acquired = await lock_dao.acquire(
            session=cast(AsyncSession, session),
            operation_scope="scope",
            resource_key="key",
            wait=False,
        )

        insert_params = session.executions[0][1]
        assert insert_params is not None
        assert insert_params["creator_id"] is None
        assert insert_params["creator_type"] == AuditActorType.SYSTEM.value

        select_stmt = session.executions[1][0]
        compiled_select = str(select_stmt.compile(dialect=mysql.dialect()))

        assert acquired is True
        assert "FOR UPDATE NOWAIT" in compiled_select

    @pytest.mark.asyncio
    @pytest.mark.parametrize("error_code", [1205, 1213, 3572])
    async def test_try_mode_returns_false_when_lock_is_unavailable(
        self,
        lock_dao: ScopedOperationLockDao,
        error_code: int,
    ):
        session = FakeSession(
            dialect=mysql.dialect(),
            select_error=_mysql_lock_error(error_code),
        )

        acquired = await lock_dao.acquire(
            session=cast(AsyncSession, session),
            operation_scope="scope",
            resource_key="key",
            wait=False,
        )

        assert acquired is False

    @pytest.mark.asyncio
    async def test_wait_mode_reraises_database_error(
        self,
        lock_dao: ScopedOperationLockDao,
    ):
        session = FakeSession(
            dialect=mysql.dialect(),
            select_error=_mysql_lock_error(3572),
        )

        with pytest.raises(OperationalError):
            await lock_dao.acquire(
                session=cast(AsyncSession, session),
                operation_scope="scope",
                resource_key="key",
                wait=True,
            )

    @pytest.mark.asyncio
    async def test_rejects_session_without_transaction(
        self,
        lock_dao: ScopedOperationLockDao,
    ):
        session = FakeSession(dialect=mysql.dialect(), in_transaction=False)

        with pytest.raises(RuntimeError, match="requires an active transaction"):
            await lock_dao.acquire(
                session=cast(AsyncSession, session),
                operation_scope="scope",
                resource_key="key",
                wait=True,
            )

        assert session.executions == []

    @pytest.mark.asyncio
    async def test_rejects_non_mysql_session(self, lock_dao: ScopedOperationLockDao):
        session = FakeSession(dialect=sqlite.dialect())

        with pytest.raises(RuntimeError, match="requires MySQL/MariaDB, got sqlite"):
            await lock_dao.acquire(
                session=cast(AsyncSession, session),
                operation_scope="scope",
                resource_key="key",
                wait=True,
            )

        assert session.executions == []


class TestScopedOperationLockService:
    @pytest.mark.parametrize(
        ("operation_scope", "resource_key", "message"),
        [
            ("", "key", "operation_scope 不能为空"),
            ("   ", "key", "operation_scope 不能为空"),
            ("a" * 65, "key", "operation_scope 长度不能超过 64 个字符"),
            ("scope", "", "resource_key 不能为空"),
            ("scope", "   ", "resource_key 不能为空"),
            ("scope", "b" * 129, "resource_key 长度不能超过 128 个字符"),
        ],
    )
    def test_validate_parameters(
        self,
        lock_service: ScopedOperationLockService,
        operation_scope: str,
        resource_key: str,
        message: str,
    ):
        with pytest.raises(AppException) as exc_info:
            lock_service._validate_parameters(operation_scope, resource_key)

        assert message in str(exc_info.value.message)

    @pytest.mark.asyncio
    async def test_rejects_invalid_mode(self, lock_service: ScopedOperationLockService):
        session = FakeSession(dialect=mysql.dialect())

        with pytest.raises(AppException) as exc_info:
            await lock_service.acquire_lock(
                session=cast(AsyncSession, session),
                operation_scope="scope",
                resource_key="key",
                mode="wait",  # type: ignore[arg-type]
            )

        assert exc_info.value.message == "mode 必须是 LockMode"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("mode", "expected_nowait"),
        [
            (LockMode.WAIT, False),
            (LockMode.TRY, True),
        ],
    )
    async def test_maps_lock_mode(
        self,
        lock_service: ScopedOperationLockService,
        mode: LockMode,
        expected_nowait: bool,
    ):
        session = FakeSession(dialect=mysql.dialect())

        acquired = await lock_service.acquire_lock(
            session=cast(AsyncSession, session),
            operation_scope="scope",
            resource_key="key",
            mode=mode,
            audit_actor=AuditActor.user(123),
        )

        select_stmt = session.executions[1][0]

        assert acquired is True
        assert select_stmt._for_update_arg.nowait is expected_nowait
