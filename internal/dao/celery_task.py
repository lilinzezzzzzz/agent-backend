from datetime import datetime

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from internal.infra.database import get_read_session, get_session
from internal.models.celery_task import CeleryTaskRecord
from internal.schemas.celery_task import CeleryTaskStatus
from pkg.database.base import SessionProvider


_RECONCILE_EXPIRED_RUNNING_SQL = text(
    """
    WITH candidates AS (
        SELECT id
        FROM celery_task_record
        WHERE status = 'RUNNING'
          AND lease_expires_at < CURRENT_TIMESTAMP
        ORDER BY lease_expires_at, id
        FOR UPDATE SKIP LOCKED
        LIMIT :batch_size
    )
    UPDATE celery_task_record AS r
    SET status = 'NEEDS_RECONCILIATION',
        lease_owner = NULL,
        lease_expires_at = NULL,
        error_type = 'WORKER_LOST_OR_TIMEOUT',
        error_message = 'running task lease expired before terminal status was persisted',
        updated_at = CURRENT_TIMESTAMP
    FROM candidates AS c
    WHERE r.id = c.id
      AND r.status = 'RUNNING'
      AND r.lease_expires_at < CURRENT_TIMESTAMP
    RETURNING r.id
    """
)

_DETECT_ORPHANED_SQL = text(
    """
    WITH candidates AS (
        SELECT id
        FROM celery_task_record
        WHERE status = 'PUBLISHED'
          AND updated_at < CURRENT_TIMESTAMP - (:orphan_seconds * INTERVAL '1 second')
        ORDER BY updated_at, id
        FOR UPDATE SKIP LOCKED
        LIMIT :batch_size
    )
    UPDATE celery_task_record AS r
    SET status = 'ORPHANED',
        error_type = 'DELIVERY_ORPHANED',
        error_message = 'published task was not claimed before orphan deadline',
        updated_at = CURRENT_TIMESTAMP
    FROM candidates AS c
    WHERE r.id = c.id
      AND r.status = 'PUBLISHED'
      AND r.updated_at < CURRENT_TIMESTAMP - (:orphan_seconds * INTERVAL '1 second')
    RETURNING r.id
    """
)


class CeleryTaskDao:
    """Celery 任务事实记录的 PostgreSQL 数据访问。"""

    def __init__(
        self,
        *,
        session_provider: SessionProvider,
        read_session_provider: SessionProvider | None = None,
    ) -> None:
        self._session_provider = session_provider
        self._read_session_provider = read_session_provider or session_provider

    @property
    def session_provider(self) -> SessionProvider:
        return self._session_provider

    async def reserve_task(
        self,
        *,
        session: AsyncSession,
        record_id: int,
        task_name: str,
        trace_id: str,
        scope: str,
        idempotency_key_hash: str,
        payload_hash: str,
        execution_timeout_seconds: int,
        idempotency_expires_at: datetime,
    ) -> tuple[CeleryTaskRecord, bool]:
        """在调用方事务内幂等登记任务。"""
        self._require_postgresql(session)
        insert_record = (
            pg_insert(CeleryTaskRecord)
            .values(
                id=record_id,
                task_name=task_name,
                trace_id=trace_id,
                scope=scope,
                idempotency_key_hash=idempotency_key_hash,
                payload_hash=payload_hash,
                status=CeleryTaskStatus.PENDING_PUBLISH.value,
                execution_timeout_seconds=execution_timeout_seconds,
                idempotency_expires_at=idempotency_expires_at,
                created_at=text("CURRENT_TIMESTAMP"),
                updated_at=text("CURRENT_TIMESTAMP"),
            )
            .on_conflict_do_nothing(
                index_elements=[
                    CeleryTaskRecord.scope,
                    CeleryTaskRecord.task_name,
                    CeleryTaskRecord.idempotency_key_hash,
                ]
            )
            .returning(CeleryTaskRecord)
        )
        record = (await session.execute(insert_record)).scalar_one_or_none()
        if record is None:
            existing_stmt = select(CeleryTaskRecord).where(
                CeleryTaskRecord.scope == scope,
                CeleryTaskRecord.task_name == task_name,
                CeleryTaskRecord.idempotency_key_hash == idempotency_key_hash,
            )
            return (await session.execute(existing_stmt)).scalar_one(), False

        return record, True

    async def get_by_id_and_scope(
        self, *, record_id: int, scope: str
    ) -> CeleryTaskRecord | None:
        """从主库读取指定 scope 的任务记录。"""
        async with self._session_provider() as session:
            result = await session.execute(
                select(CeleryTaskRecord).where(
                    CeleryTaskRecord.id == record_id,
                    CeleryTaskRecord.scope == scope,
                )
            )
            return result.scalar_one_or_none()

    async def claim_execution(
        self,
        *,
        record_id: int,
        task_name: str,
        scope: str,
        owner: str,
        lease_seconds: int,
        execution_timeout_seconds: int,
    ) -> CeleryTaskRecord | None:
        """仅允许 PUBLISHED 任务进入一次 RUNNING。"""
        async with self._session_provider() as session, session.begin():
            self._require_postgresql(session)
            stmt = (
                update(CeleryTaskRecord)
                .where(
                    CeleryTaskRecord.id == record_id,
                    CeleryTaskRecord.task_name == task_name,
                    CeleryTaskRecord.scope == scope,
                    CeleryTaskRecord.status == CeleryTaskStatus.PUBLISHED.value,
                    CeleryTaskRecord.execution_timeout_seconds
                    == execution_timeout_seconds,
                )
                .values(
                    status=CeleryTaskStatus.RUNNING.value,
                    lease_owner=owner,
                    lease_expires_at=text(
                        f"CURRENT_TIMESTAMP + ({int(lease_seconds)} * INTERVAL '1 second')"
                    ),
                    error_type=None,
                    error_message=None,
                    updated_at=text("CURRENT_TIMESTAMP"),
                )
                .returning(CeleryTaskRecord)
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def finish_execution(
        self,
        *,
        record_id: int,
        owner: str,
        status: CeleryTaskStatus,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """使用 owner fencing 将 RUNNING 任务写入终态。"""
        if status not in {CeleryTaskStatus.SUCCEEDED, CeleryTaskStatus.FAILED}:
            raise ValueError(f"unsupported automatic terminal status: {status}")
        async with self._session_provider() as session, session.begin():
            self._require_postgresql(session)
            stmt = (
                update(CeleryTaskRecord)
                .where(
                    CeleryTaskRecord.id == record_id,
                    CeleryTaskRecord.status == CeleryTaskStatus.RUNNING.value,
                    CeleryTaskRecord.lease_owner == owner,
                )
                .values(
                    status=status.value,
                    lease_owner=None,
                    lease_expires_at=None,
                    error_type=error_type,
                    error_message=error_message,
                    updated_at=text("CURRENT_TIMESTAMP"),
                )
                .returning(CeleryTaskRecord.id)
            )
            return (await session.execute(stmt)).scalar_one_or_none() is not None

    async def mark_published(self, *, record_id: int) -> bool:
        """将已确认提交到 broker 的任务标记为 PUBLISHED。"""
        async with self._session_provider() as session, session.begin():
            self._require_postgresql(session)
            stmt = (
                update(CeleryTaskRecord)
                .where(
                    CeleryTaskRecord.id == record_id,
                    CeleryTaskRecord.status == CeleryTaskStatus.PENDING_PUBLISH.value,
                )
                .values(
                    status=CeleryTaskStatus.PUBLISHED.value,
                    error_type=None,
                    error_message=None,
                    updated_at=text("CURRENT_TIMESTAMP"),
                )
                .returning(CeleryTaskRecord.id)
            )
            return (await session.execute(stmt)).scalar_one_or_none() is not None

    async def mark_publish_failed(self, *, record_id: int) -> bool:
        """将明确失败的 broker 提交标记为 PUBLISH_FAILED。"""
        async with self._session_provider() as session, session.begin():
            self._require_postgresql(session)
            stmt = (
                update(CeleryTaskRecord)
                .where(
                    CeleryTaskRecord.id == record_id,
                    CeleryTaskRecord.status == CeleryTaskStatus.PENDING_PUBLISH.value,
                )
                .values(
                    status=CeleryTaskStatus.PUBLISH_FAILED.value,
                    error_type="BROKER_PUBLISH_FAILED",
                    error_message="broker publish failed; automatic retry is disabled",
                    updated_at=text("CURRENT_TIMESTAMP"),
                )
                .returning(CeleryTaskRecord.id)
            )
            return (await session.execute(stmt)).scalar_one_or_none() is not None

    async def fail_stale_pending_publish(
        self, *, batch_size: int, stale_seconds: int
    ) -> list[int]:
        """批量将超时未确认发布的任务收敛为 PUBLISH_FAILED。"""
        async with self._session_provider() as session, session.begin():
            self._require_postgresql(session)
            candidates = (
                select(CeleryTaskRecord.id)
                .where(
                    CeleryTaskRecord.status == CeleryTaskStatus.PENDING_PUBLISH.value,
                    CeleryTaskRecord.updated_at
                    < text(
                        f"CURRENT_TIMESTAMP - ({int(stale_seconds)} * INTERVAL '1 second')"
                    ),
                )
                .order_by(CeleryTaskRecord.updated_at, CeleryTaskRecord.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            stmt = (
                update(CeleryTaskRecord)
                .where(CeleryTaskRecord.id.in_(candidates))
                .values(
                    status=CeleryTaskStatus.PUBLISH_FAILED.value,
                    error_type="PUBLISH_CONFIRMATION_TIMEOUT",
                    error_message="task publish was not confirmed before the deadline",
                    updated_at=text("CURRENT_TIMESTAMP"),
                )
                .returning(CeleryTaskRecord.id)
            )
            return list((await session.execute(stmt)).scalars().all())

    async def reconcile_expired_running(self, *, batch_size: int) -> list[int]:
        """将过期 RUNNING 任务保守收敛为 NEEDS_RECONCILIATION。"""
        async with self._session_provider() as session, session.begin():
            self._require_postgresql(session)
            result = await session.execute(
                _RECONCILE_EXPIRED_RUNNING_SQL, {"batch_size": batch_size}
            )
            return list(result.scalars().all())

    async def detect_orphaned(
        self, *, batch_size: int, orphan_seconds: int
    ) -> list[int]:
        """将超时未 claim 的 PUBLISHED 任务收敛为 ORPHANED。"""
        async with self._session_provider() as session, session.begin():
            self._require_postgresql(session)
            result = await session.execute(
                _DETECT_ORPHANED_SQL,
                {"batch_size": batch_size, "orphan_seconds": orphan_seconds},
            )
            return list(result.scalars().all())

    @staticmethod
    def _require_postgresql(session: AsyncSession) -> None:
        dialect_name = session.get_bind().dialect.name
        if dialect_name != "postgresql":
            raise RuntimeError(
                f"Celery idempotency requires PostgreSQL, got {dialect_name}"
            )


_celery_task_dao: CeleryTaskDao | None = None


def new_celery_task_dao() -> CeleryTaskDao:
    """获取 CeleryTaskDao 单例。"""
    global _celery_task_dao
    if _celery_task_dao is None:
        _celery_task_dao = CeleryTaskDao(
            session_provider=get_session,
            read_session_provider=get_read_session,
        )
    return _celery_task_dao
