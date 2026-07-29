import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from internal.config import settings
from internal.core import AppException, errors
from internal.dao.celery_task import CeleryTaskDao, new_celery_task_dao
from internal.models.celery_task import CeleryTaskRecord
from internal.schemas.celery_task import (
    CeleryTaskCancelDTO,
    CeleryTaskDetailDTO,
    CeleryTaskDispatchDTO,
    CeleryTaskErrorCode,
    CeleryTaskStatus,
)
from pkg.celery_queue import CeleryClient
from pkg.database.session import SessionProvider
from pkg.ids import snowflake_id_generator
from pkg.logger import logger


_ALLOWED_OPTIONS = frozenset({"priority", "expires"})


def canonical_task_payload(
    *,
    task_name: str,
    args: Sequence[object],
    kwargs: Mapping[str, object],
    queue: str,
    options: Mapping[str, object],
) -> bytes:
    """生成用于幂等比对的 canonical JSON。"""
    unsupported = set(options).difference(_ALLOWED_OPTIONS)
    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise ValueError(f"unsupported Celery publish options: {names}")
    try:
        return json.dumps(
            {
                "task_name": task_name,
                "args": list(args),
                "kwargs": dict(kwargs),
                "queue": queue,
                "options": dict(options),
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("Celery task payload must be JSON serializable") from exc


class CeleryTaskService:
    """管理 Celery 逻辑任务的登记、查询、执行和取消状态。"""

    def __init__(
        self,
        *,
        dao: CeleryTaskDao,
        session_provider: SessionProvider,
        celery_client: CeleryClient,
    ) -> None:
        self._dao = dao
        self._session_provider = session_provider
        self._celery_client = celery_client

    async def submit_once(
        self,
        *,
        task_name: str,
        trace_id: str,
        scope: str,
        idempotency_key: str,
        queue: str,
        args: Sequence[object] = (),
        kwargs: Mapping[str, object] | None = None,
        options: Mapping[str, object] | None = None,
    ) -> CeleryTaskDispatchDTO:
        """登记逻辑任务，提交到 Broker，并确认排队状态。"""
        self._validate_submission(
            task_name=task_name,
            trace_id=trace_id,
            scope=scope,
            idempotency_key=idempotency_key,
            queue=queue,
        )
        task_kwargs = dict(kwargs or {})
        publish_options: dict[str, Any] = dict(options or {})
        payload = canonical_task_payload(
            task_name=task_name,
            args=args,
            kwargs=task_kwargs,
            queue=queue,
            options=publish_options,
        )
        max_payload_bytes = settings.CELERY_TASK_MAX_PAYLOAD_BYTES
        if len(payload) > max_payload_bytes:
            raise AppException(
                errors.PayloadTooLarge,
                message=f"Celery task payload exceeds {max_payload_bytes} bytes",
            )

        idempotency_key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        payload_hash = hashlib.sha256(payload).hexdigest()
        async with self._session_provider() as session, session.begin():
            record, created = await self._dao.reserve_task(
                session=session,
                record_id=snowflake_id_generator.generate(),
                task_name=task_name,
                trace_id=trace_id,
                scope=scope,
                queue=queue,
                idempotency_key_hash=idempotency_key_hash,
                payload_hash=payload_hash,
            )
        if not created and record.payload_hash != payload_hash:
            raise AppException(
                errors.IdempotencyConflict,
                message="same idempotency key was submitted with a different payload",
            )
        if not created:
            return self._to_dispatch_dto(record, created=False)

        try:
            await self._celery_client.async_submit(
                task_name=task_name,
                trace_id=trace_id,
                task_id=record.task_id,
                args=(record.id, scope, *args),
                kwargs=task_kwargs,
                queue=queue,
                retry=False,
                **publish_options,
            )
        except Exception as exc:
            current, marked_failed = await self._dao.mark_dispatch_failed(
                record_id=record.id,
                scope=scope,
            )
            if marked_failed or current is None:
                raise AppException(
                    errors.ServiceUnavailable, message="Celery task publish failed"
                ) from exc
            logger.warning(
                "Celery publish raised after task state advanced; preserving database state: "
                f"record_id={record.id}, status={current.status}"
            )
            return self._to_dispatch_dto(current, created=True)

        current = await self._dao.mark_queued(
            record_id=record.id,
            scope=scope,
            queue_start_timeout_seconds=settings.CELERY_QUEUE_START_TIMEOUT_SECONDS,
        )
        if current is None:
            raise RuntimeError("Celery task disappeared after broker publish")
        return self._to_dispatch_dto(current, created=True)

    async def get_task(self, *, record_id: int, scope: str) -> CeleryTaskDetailDTO:
        """查询当前 scope 可见的逻辑任务。"""
        record = await self._dao.get_by_id_and_scope(record_id=record_id, scope=scope)
        if record is None:
            raise AppException(errors.NotFound, message="Celery task record not found")
        if record.updated_at is None:
            raise RuntimeError("Celery task record updated_at is missing")
        return self._to_detail_dto(record)

    async def cancel_task(self, *, record_id: int, scope: str) -> CeleryTaskCancelDTO:
        """幂等请求取消指定任务，并 best-effort 通知 Celery Broker。"""
        record = await self._dao.request_cancellation(
            record_id=record_id,
            scope=scope,
        )
        if record is None:
            raise AppException(errors.NotFound, message="Celery task record not found")
        status = CeleryTaskStatus(record.status)
        if status in {
            CeleryTaskStatus.SUCCEEDED,
            CeleryTaskStatus.FAILED,
            CeleryTaskStatus.ORPHANED,
        } or (status is CeleryTaskStatus.RUNNING and not record.cancel_allowed):
            raise AppException(
                errors.TaskStateConflict,
                message=f"task cannot be cancelled in current state: {status.value}",
            )

        try:
            await self._celery_client.async_revoke(record.task_id, terminate=False)
        except Exception as exc:
            logger.warning(
                "Celery revoke failed after cancellation state persisted: "
                f"record_id={record.id}, error_type={type(exc).__name__}"
            )
        return CeleryTaskCancelDTO(record_id=record.id, status=status)

    async def claim(
        self,
        *,
        record_id: int,
        task_name: str,
        scope: str,
        execution_token: str,
        execution_timeout_seconds: int,
        deadline_grace_seconds: int,
    ) -> bool:
        """尝试获取任务唯一执行权。"""
        if execution_timeout_seconds <= 0 or deadline_grace_seconds <= 0:
            raise ValueError("execution timeout and deadline grace must be positive")
        record = await self._dao.claim_execution(
            record_id=record_id,
            task_name=task_name,
            scope=scope,
            execution_token=execution_token,
            hard_deadline_seconds=execution_timeout_seconds + deadline_grace_seconds,
        )
        return record is not None

    async def acknowledge_cancellation(
        self, *, record_id: int, execution_token: str
    ) -> bool:
        """Worker 在协作检查点确认取消。"""
        return await self._dao.acknowledge_cancellation(
            record_id=record_id,
            execution_token=execution_token,
        )

    async def disallow_cancellation(
        self, *, record_id: int, execution_token: str
    ) -> bool:
        """Worker 进入不可取消阶段前关闭取消门禁。"""
        return await self._dao.disallow_cancellation(
            record_id=record_id,
            execution_token=execution_token,
        )

    async def succeed(self, *, record_id: int, execution_token: str) -> bool:
        """使用 execution token 将当前任务写入 SUCCEEDED。"""
        updated = await self._dao.finish_execution(
            record_id=record_id,
            execution_token=execution_token,
            status=CeleryTaskStatus.SUCCEEDED,
        )
        if updated:
            return True
        if await self.acknowledge_cancellation(
            record_id=record_id, execution_token=execution_token
        ):
            return False
        raise RuntimeError("Celery task success state write rejected by token fencing")

    async def fail(
        self, *, record_id: int, execution_token: str, exc: Exception
    ) -> bool:
        """使用 execution token 写入脱敏失败信息。"""
        updated = await self._dao.finish_execution(
            record_id=record_id,
            execution_token=execution_token,
            status=CeleryTaskStatus.FAILED,
            error_code=CeleryTaskErrorCode.TASK_EXECUTION_FAILED,
            error_summary=(
                f"task execution failed ({type(exc).__name__}); "
                "inspect redacted worker logs"
            ),
        )
        if updated:
            return True
        if await self.acknowledge_cancellation(
            record_id=record_id, execution_token=execution_token
        ):
            return False
        raise RuntimeError(
            "Celery task failure state write rejected by token fencing"
        ) from exc

    @staticmethod
    def _validate_submission(
        *,
        task_name: str,
        trace_id: str,
        scope: str,
        idempotency_key: str,
        queue: str,
    ) -> None:
        fields = {
            "task_name": (task_name, 255),
            "trace_id": (trace_id, 128),
            "scope": (scope, 128),
            "idempotency_key": (idempotency_key, 1024),
            "queue": (queue, 64),
        }
        for name, (value, max_length) in fields.items():
            if not value or len(value) > max_length:
                raise ValueError(f"{name} must contain 1 to {max_length} characters")

    @staticmethod
    def _to_dispatch_dto(
        record: CeleryTaskRecord, *, created: bool
    ) -> CeleryTaskDispatchDTO:
        return CeleryTaskDispatchDTO(
            record_id=record.id,
            status=CeleryTaskStatus(record.status),
            created=created,
        )

    @staticmethod
    def _to_detail_dto(record: CeleryTaskRecord) -> CeleryTaskDetailDTO:
        if record.updated_at is None:
            raise RuntimeError("Celery task record updated_at is missing")
        error_code = (
            CeleryTaskErrorCode(record.error_code) if record.error_code else None
        )
        return CeleryTaskDetailDTO(
            record_id=record.id,
            task_name=record.task_name,
            queue=record.queue,
            status=CeleryTaskStatus(record.status),
            cancel_allowed=record.cancel_allowed,
            trace_id=record.trace_id,
            attempt_count=record.attempt_count,
            queued_deadline_at=record.queued_deadline_at,
            hard_deadline_at=record.hard_deadline_at,
            fence_expires_at=record.fence_expires_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
            error_code=error_code,
            error_summary=record.error_summary,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


_celery_task_service: CeleryTaskService | None = None


def new_celery_task_service() -> CeleryTaskService:
    """获取 CeleryTaskService 单例。"""
    global _celery_task_service
    if _celery_task_service is None:
        from internal.infra.celery import celery_client

        dao = new_celery_task_dao()
        _celery_task_service = CeleryTaskService(
            dao=dao,
            session_provider=dao.session_provider,
            celery_client=celery_client,
        )
    return _celery_task_service
