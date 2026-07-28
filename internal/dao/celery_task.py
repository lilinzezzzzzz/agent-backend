from datetime import timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from internal.infra.database import get_session
from internal.models.celery_task import CeleryTaskRecord
from internal.schemas.celery_task import CeleryTaskErrorCode, CeleryTaskStatus
from pkg.database.base import AuditActor, SessionProvider


_TASK_AUDIT_ACTOR = AuditActor.task()


_FAIL_STALE_SUBMITTING_SQL = text(
    """
    WITH candidates AS (
        SELECT id
        FROM celery_task_record
        WHERE status = 'SUBMITTING'
          AND updated_at < :cutoff
        ORDER BY updated_at, id
        FOR UPDATE SKIP LOCKED
        LIMIT :batch_size
    )
    UPDATE celery_task_record AS r
    SET status = 'FAILED',
        error_code = 'PUBLISH_CONFIRMATION_TIMEOUT',
        error_summary = 'task publish was not confirmed before the deadline',
        finished_at = :now,
        updater_id = NULL,
        updater_type = 'task',
        updated_at = :now
    FROM candidates AS c
    WHERE r.id = c.id
      AND r.status = 'SUBMITTING'
      AND r.updated_at < :cutoff
    RETURNING r.id
    """
)

_FAIL_EXPIRED_QUEUED_SQL = text(
    """
    WITH candidates AS (
        SELECT id
        FROM celery_task_record
        WHERE status = 'QUEUED'
          AND queued_deadline_at <= :now
        ORDER BY queued_deadline_at, id
        FOR UPDATE SKIP LOCKED
        LIMIT :batch_size
    )
    UPDATE celery_task_record AS r
    SET status = 'FAILED',
        error_code = 'QUEUE_START_TIMEOUT',
        error_summary = 'queued task was not claimed before the start deadline',
        finished_at = :now,
        updater_id = NULL,
        updater_type = 'task',
        updated_at = :now
    FROM candidates AS c
    WHERE r.id = c.id
      AND r.status = 'QUEUED'
      AND r.queued_deadline_at <= :now
    RETURNING r.id
    """
)

_RECONCILE_EXPIRED_EXECUTION_SQL = text(
    """
    WITH candidates AS (
        SELECT id
        FROM celery_task_record
        WHERE status IN ('RUNNING', 'CANCELLING')
          AND hard_deadline_at <= :now
        ORDER BY hard_deadline_at, id
        FOR UPDATE SKIP LOCKED
        LIMIT :batch_size
    )
    UPDATE celery_task_record AS r
    SET status = 'ORPHANED',
        error_code = CASE
            WHEN r.status = 'CANCELLING' THEN 'CANCEL_DEADLINE_EXCEEDED'
            ELSE 'WORKER_LOST_OR_TIMEOUT'
        END,
        error_summary = CASE
            WHEN r.status = 'CANCELLING' THEN 'task cancellation exceeded the execution deadline'
            ELSE 'running task exceeded the execution deadline'
        END,
        fence_expires_at = :fence_expires_at,
        updater_id = NULL,
        updater_type = 'task',
        updated_at = :now
    FROM candidates AS c
    WHERE r.id = c.id
      AND r.status IN ('RUNNING', 'CANCELLING')
      AND r.hard_deadline_at <= :now
    RETURNING r.id
    """
)


class CeleryTaskDao:
    """Celery 任务事实记录的 PostgreSQL 数据访问。"""

    def __init__(
        self,
        *,
        session_provider: SessionProvider,
    ) -> None:
        self._session_provider = session_provider

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
        queue: str,
        idempotency_key_hash: str,
        payload_hash: str,
    ) -> tuple[CeleryTaskRecord, bool]:
        """在调用方事务内幂等登记 SUBMITTING 任务。"""
        self._require_postgresql(session)
        now = await CeleryTaskRecord.get_database_now(session)
        insert_record = (
            pg_insert(CeleryTaskRecord)
            .values(
                id=record_id,
                task_name=task_name,
                trace_id=trace_id,
                scope=scope,
                queue=queue,
                idempotency_key_hash=idempotency_key_hash,
                payload_hash=payload_hash,
                status=CeleryTaskStatus.SUBMITTING.value,
                attempt_count=0,
                **_TASK_AUDIT_ACTOR.creator_values(),
                updater_id=None,
                updater_type=None,
                created_at=now,
                updated_at=now,
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
            return await self._get_by_id_and_scope_in_session(
                session, record_id=record_id, scope=scope
            )

    async def mark_queued(
        self,
        *,
        record_id: int,
        scope: str,
        queue_start_timeout_seconds: int,
    ) -> CeleryTaskRecord | None:
        """Broker 接受消息后将 SUBMITTING CAS 为 QUEUED。"""
        async with self._session_provider() as session, session.begin():
            self._require_postgresql(session)
            now = await CeleryTaskRecord.get_database_now(session)
            stmt = (
                update(CeleryTaskRecord)
                .where(
                    CeleryTaskRecord.id == record_id,
                    CeleryTaskRecord.scope == scope,
                    CeleryTaskRecord.status == CeleryTaskStatus.SUBMITTING.value,
                )
                .values(
                    status=CeleryTaskStatus.QUEUED.value,
                    queued_deadline_at=now
                    + timedelta(seconds=queue_start_timeout_seconds),
                    **_TASK_AUDIT_ACTOR.updater_values(),
                    updated_at=now,
                )
                .returning(CeleryTaskRecord)
            )
            record = (await session.execute(stmt)).scalar_one_or_none()
            if record is not None:
                return record
            return await self._get_by_id_and_scope_in_session(
                session, record_id=record_id, scope=scope
            )

    async def mark_dispatch_failed(
        self, *, record_id: int, scope: str
    ) -> tuple[CeleryTaskRecord | None, bool]:
        """将仍为 SUBMITTING 的任务 CAS 为发布失败。"""
        async with self._session_provider() as session, session.begin():
            self._require_postgresql(session)
            now = await CeleryTaskRecord.get_database_now(session)
            stmt = (
                update(CeleryTaskRecord)
                .where(
                    CeleryTaskRecord.id == record_id,
                    CeleryTaskRecord.scope == scope,
                    CeleryTaskRecord.status == CeleryTaskStatus.SUBMITTING.value,
                )
                .values(
                    status=CeleryTaskStatus.FAILED.value,
                    error_code=CeleryTaskErrorCode.BROKER_PUBLISH_FAILED.value,
                    error_summary="broker publish failed; automatic retry is disabled",
                    finished_at=now,
                    **_TASK_AUDIT_ACTOR.updater_values(),
                    updated_at=now,
                )
                .returning(CeleryTaskRecord)
            )
            record = (await session.execute(stmt)).scalar_one_or_none()
            if record is not None:
                return record, True
            return (
                await self._get_by_id_and_scope_in_session(
                    session, record_id=record_id, scope=scope
                ),
                False,
            )

    async def claim_execution(
        self,
        *,
        record_id: int,
        task_name: str,
        scope: str,
        execution_token: str,
        hard_deadline_seconds: int,
    ) -> CeleryTaskRecord | None:
        """允许 SUBMITTING 或 QUEUED delivery 获取唯一执行权。"""
        async with self._session_provider() as session, session.begin():
            self._require_postgresql(session)
            now = await CeleryTaskRecord.get_database_now(session)
            stmt = (
                update(CeleryTaskRecord)
                .where(
                    CeleryTaskRecord.id == record_id,
                    CeleryTaskRecord.task_name == task_name,
                    CeleryTaskRecord.scope == scope,
                    CeleryTaskRecord.status.in_(
                        [
                            CeleryTaskStatus.SUBMITTING.value,
                            CeleryTaskStatus.QUEUED.value,
                        ]
                    ),
                )
                .values(
                    status=CeleryTaskStatus.RUNNING.value,
                    cancel_allowed=True,
                    execution_token=execution_token,
                    attempt_count=CeleryTaskRecord.attempt_count + 1,
                    started_at=func.coalesce(CeleryTaskRecord.started_at, now),
                    hard_deadline_at=now + timedelta(seconds=hard_deadline_seconds),
                    error_code=None,
                    error_summary=None,
                    **_TASK_AUDIT_ACTOR.updater_values(),
                    updated_at=now,
                )
                .returning(CeleryTaskRecord)
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def finish_execution(
        self,
        *,
        record_id: int,
        execution_token: str,
        status: CeleryTaskStatus,
        error_code: CeleryTaskErrorCode | None = None,
        error_summary: str | None = None,
    ) -> bool:
        """使用 execution token fencing 将 RUNNING 任务写入终态。"""
        if status not in {CeleryTaskStatus.SUCCEEDED, CeleryTaskStatus.FAILED}:
            raise ValueError(f"unsupported automatic terminal status: {status}")
        async with self._session_provider() as session, session.begin():
            self._require_postgresql(session)
            now = await CeleryTaskRecord.get_database_now(session)
            stmt = (
                update(CeleryTaskRecord)
                .where(
                    CeleryTaskRecord.id == record_id,
                    CeleryTaskRecord.status == CeleryTaskStatus.RUNNING.value,
                    CeleryTaskRecord.execution_token == execution_token,
                )
                .values(
                    status=status.value,
                    error_code=error_code.value if error_code else None,
                    error_summary=error_summary[:512] if error_summary else None,
                    finished_at=now,
                    **_TASK_AUDIT_ACTOR.updater_values(),
                    updated_at=now,
                )
                .returning(CeleryTaskRecord.id)
            )
            return (await session.execute(stmt)).scalar_one_or_none() is not None

    async def acknowledge_cancellation(
        self, *, record_id: int, execution_token: str
    ) -> bool:
        """Worker 在协作检查点确认取消并写入 CANCELLED。"""
        async with self._session_provider() as session, session.begin():
            self._require_postgresql(session)
            now = await CeleryTaskRecord.get_database_now(session)
            stmt = (
                update(CeleryTaskRecord)
                .where(
                    CeleryTaskRecord.id == record_id,
                    CeleryTaskRecord.status == CeleryTaskStatus.CANCELLING.value,
                    CeleryTaskRecord.execution_token == execution_token,
                )
                .values(
                    status=CeleryTaskStatus.CANCELLED.value,
                    finished_at=now,
                    **_TASK_AUDIT_ACTOR.updater_values(),
                    updated_at=now,
                )
                .returning(CeleryTaskRecord.id)
            )
            return (await session.execute(stmt)).scalar_one_or_none() is not None

    async def request_cancellation(
        self, *, record_id: int, scope: str
    ) -> CeleryTaskRecord | None:
        """锁定记录并按当前状态提交幂等取消请求。"""
        async with self._session_provider() as session, session.begin():
            self._require_postgresql(session)
            stmt = (
                select(CeleryTaskRecord)
                .where(
                    CeleryTaskRecord.id == record_id,
                    CeleryTaskRecord.scope == scope,
                )
                .with_for_update()
            )
            record = (await session.execute(stmt)).scalar_one_or_none()
            if record is None:
                return None

            status = CeleryTaskStatus(record.status)
            if status in {
                CeleryTaskStatus.CANCELLING,
                CeleryTaskStatus.CANCELLED,
                CeleryTaskStatus.ORPHANED,
            }:
                return record
            if status.is_terminal:
                return record

            if status is CeleryTaskStatus.RUNNING and not record.cancel_allowed:
                return record

            now = await CeleryTaskRecord.get_database_now(session)
            if status in {CeleryTaskStatus.SUBMITTING, CeleryTaskStatus.QUEUED}:
                record.status = CeleryTaskStatus.CANCELLED.value
                record.finished_at = now
            elif status is CeleryTaskStatus.RUNNING:
                record.status = CeleryTaskStatus.CANCELLING.value
            record.updater_id = _TASK_AUDIT_ACTOR.actor_id
            record.updater_type = _TASK_AUDIT_ACTOR.actor_type.value
            record.updated_at = now
            return record

    async def disallow_cancellation(
        self, *, record_id: int, execution_token: str
    ) -> bool:
        """Worker 进入不可取消阶段前关闭取消门禁。"""
        async with self._session_provider() as session, session.begin():
            self._require_postgresql(session)
            now = await CeleryTaskRecord.get_database_now(session)
            stmt = (
                update(CeleryTaskRecord)
                .where(
                    CeleryTaskRecord.id == record_id,
                    CeleryTaskRecord.status == CeleryTaskStatus.RUNNING.value,
                    CeleryTaskRecord.execution_token == execution_token,
                    CeleryTaskRecord.cancel_allowed.is_(True),
                )
                .values(
                    cancel_allowed=False,
                    **_TASK_AUDIT_ACTOR.updater_values(),
                    updated_at=now,
                )
                .returning(CeleryTaskRecord.id)
            )
            return (await session.execute(stmt)).scalar_one_or_none() is not None

    async def fail_stale_submitting(
        self, *, batch_size: int, stale_seconds: int
    ) -> list[int]:
        """批量将发布确认超时的 SUBMITTING 任务收敛为 FAILED。"""
        async with self._session_provider() as session, session.begin():
            self._require_postgresql(session)
            now = await CeleryTaskRecord.get_database_now(session)
            result = await session.execute(
                _FAIL_STALE_SUBMITTING_SQL,
                {
                    "batch_size": batch_size,
                    "cutoff": now - timedelta(seconds=stale_seconds),
                    "now": now,
                },
            )
            return list(result.scalars().all())

    async def fail_expired_queued(self, *, batch_size: int) -> list[int]:
        """批量将超过启动 deadline 的 QUEUED 任务收敛为 FAILED。"""
        async with self._session_provider() as session, session.begin():
            self._require_postgresql(session)
            now = await CeleryTaskRecord.get_database_now(session)
            result = await session.execute(
                _FAIL_EXPIRED_QUEUED_SQL,
                {"batch_size": batch_size, "now": now},
            )
            return list(result.scalars().all())

    async def reconcile_expired_execution(
        self, *, batch_size: int, orphan_fence_seconds: int
    ) -> list[int]:
        """批量将超过 hard deadline 的运行中任务隔离为 ORPHANED。"""
        async with self._session_provider() as session, session.begin():
            self._require_postgresql(session)
            now = await CeleryTaskRecord.get_database_now(session)
            result = await session.execute(
                _RECONCILE_EXPIRED_EXECUTION_SQL,
                {
                    "batch_size": batch_size,
                    "fence_expires_at": now + timedelta(seconds=orphan_fence_seconds),
                    "now": now,
                },
            )
            return list(result.scalars().all())

    async def resolve_orphaned(
        self,
        *,
        record_id: int,
        scope: str,
        status: CeleryTaskStatus,
        error_code: CeleryTaskErrorCode | None = None,
        error_summary: str | None = None,
    ) -> bool:
        """核对完成后将 fence 到期的 ORPHANED 任务写入最终状态。"""
        if status not in {CeleryTaskStatus.FAILED, CeleryTaskStatus.CANCELLED}:
            raise ValueError(f"unsupported orphan resolution status: {status}")
        async with self._session_provider() as session, session.begin():
            self._require_postgresql(session)
            now = await CeleryTaskRecord.get_database_now(session)
            values = {
                "status": status.value,
                "finished_at": now,
                **_TASK_AUDIT_ACTOR.updater_values(),
                "updated_at": now,
            }
            if error_code is not None:
                values["error_code"] = error_code.value
            if error_summary is not None:
                values["error_summary"] = error_summary[:512]
            stmt = (
                update(CeleryTaskRecord)
                .where(
                    CeleryTaskRecord.id == record_id,
                    CeleryTaskRecord.scope == scope,
                    CeleryTaskRecord.status == CeleryTaskStatus.ORPHANED.value,
                    CeleryTaskRecord.fence_expires_at <= now,
                )
                .values(**values)
                .returning(CeleryTaskRecord.id)
            )
            return (await session.execute(stmt)).scalar_one_or_none() is not None

    @staticmethod
    async def _get_by_id_and_scope_in_session(
        session: AsyncSession, *, record_id: int, scope: str
    ) -> CeleryTaskRecord | None:
        result = await session.execute(
            select(CeleryTaskRecord).where(
                CeleryTaskRecord.id == record_id,
                CeleryTaskRecord.scope == scope,
            )
        )
        return result.scalar_one_or_none()

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
        )
    return _celery_task_dao
