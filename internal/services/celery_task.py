import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from internal.config import settings
from internal.core import AppException, errors
from internal.dao.celery_task import CeleryTaskDao, new_celery_task_dao
from internal.schemas.celery_task import (
    CeleryTaskDetailDTO,
    CeleryTaskDispatchDTO,
    CeleryTaskStatus,
)
from pkg.celery_queue import CeleryClient
from pkg.database.base import SessionProvider
from pkg.ids import snowflake_id_generator


_ALLOWED_OPTIONS = frozenset({"priority", "expires"})


def canonical_task_payload(
    *,
    task_name: str,
    args: Sequence[object],
    kwargs: Mapping[str, object],
    queue: str | None,
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
    """管理 Celery 逻辑任务的登记、查询和 Worker 执行状态。"""

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
        args: Sequence[object] = (),
        kwargs: Mapping[str, object] | None = None,
        queue: str | None = None,
        options: Mapping[str, object] | None = None,
        execution_timeout_seconds: int,
        idempotency_expires_in: timedelta,
    ) -> CeleryTaskDispatchDTO:
        """登记逻辑任务，提交到 broker，并确认发布状态。"""
        self._validate_submission(
            task_name=task_name,
            trace_id=trace_id,
            scope=scope,
            idempotency_key=idempotency_key,
            execution_timeout_seconds=execution_timeout_seconds,
            idempotency_expires_in=idempotency_expires_in,
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

        now = datetime.now(UTC)
        idempotency_key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        payload_hash = hashlib.sha256(payload).hexdigest()
        async with self._session_provider() as session, session.begin():
            record, created = await self._dao.reserve_task(
                session=session,
                record_id=snowflake_id_generator.generate(),
                task_name=task_name,
                trace_id=trace_id,
                scope=scope,
                idempotency_key_hash=idempotency_key_hash,
                payload_hash=payload_hash,
                execution_timeout_seconds=execution_timeout_seconds,
                idempotency_expires_at=now + idempotency_expires_in,
            )
        if not created and record.payload_hash != payload_hash:
            raise AppException(
                errors.IdempotencyConflict,
                message="same idempotency key was submitted with a different payload",
            )
        if not created:
            return CeleryTaskDispatchDTO(
                record_id=record.id,
                status=CeleryTaskStatus(record.status),
                created=False,
            )

        try:
            await self._celery_client.async_submit(
                task_name=task_name,
                trace_id=trace_id,
                task_id=record.task_id,
                args=(record.id, scope, *args),
                kwargs=task_kwargs,
                queue=queue,
                countdown=settings.CELERY_TASK_DELIVERY_GRACE_SECONDS,
                retry=False,
                **publish_options,
            )
        except Exception as exc:
            await self._dao.mark_publish_failed(record_id=record.id)
            raise AppException(
                errors.ServiceUnavailable, message="Celery task publish failed"
            ) from exc
        if not await self._dao.mark_published(record_id=record.id):
            raise RuntimeError("Celery task publish confirmation state write failed")
        return CeleryTaskDispatchDTO(
            record_id=record.id,
            status=CeleryTaskStatus.PUBLISHED,
            created=True,
        )

    async def get_task(self, *, record_id: int, scope: str) -> CeleryTaskDetailDTO:
        """查询当前 scope 可见的逻辑任务。"""
        record = await self._dao.get_by_id_and_scope(record_id=record_id, scope=scope)
        if record is None:
            raise AppException(errors.NotFound, message="Celery task record not found")
        return CeleryTaskDetailDTO(
            record_id=record.id,
            task_name=record.task_name,
            status=CeleryTaskStatus(record.status),
            trace_id=record.trace_id,
            error_type=record.error_type,
            error_message=record.error_message,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _validate_submission(
        *,
        task_name: str,
        trace_id: str,
        scope: str,
        idempotency_key: str,
        execution_timeout_seconds: int,
        idempotency_expires_in: timedelta,
    ) -> None:
        fields = {
            "task_name": (task_name, 255),
            "trace_id": (trace_id, 128),
            "scope": (scope, 128),
            "idempotency_key": (idempotency_key, 1024),
        }
        for name, (value, max_length) in fields.items():
            if not value or len(value) > max_length:
                raise ValueError(f"{name} must contain 1 to {max_length} characters")
        if execution_timeout_seconds <= 0:
            raise ValueError("execution_timeout_seconds must be positive")
        if idempotency_expires_in <= timedelta(0):
            raise ValueError("idempotency_expires_in must be positive")

    async def claim(
        self,
        *,
        record_id: int,
        task_name: str,
        scope: str,
        owner: str,
        execution_timeout_seconds: int,
        lease_grace_seconds: int,
    ) -> bool:
        """尝试获取任务唯一执行权。"""
        record = await self._dao.claim_execution(
            record_id=record_id,
            task_name=task_name,
            scope=scope,
            owner=owner,
            lease_seconds=execution_timeout_seconds + lease_grace_seconds,
            execution_timeout_seconds=execution_timeout_seconds,
        )
        return record is not None

    async def succeed(self, *, record_id: int, owner: str) -> None:
        """将当前 owner 的任务写入 SUCCEEDED。"""
        updated = await self._dao.finish_execution(
            record_id=record_id,
            owner=owner,
            status=CeleryTaskStatus.SUCCEEDED,
        )
        if not updated:
            raise RuntimeError(
                "Celery task success state write rejected by owner fencing"
            )

    async def fail(self, *, record_id: int, owner: str, exc: Exception) -> None:
        """将明确的业务异常写入 FAILED，不持久化原始敏感消息。"""
        updated = await self._dao.finish_execution(
            record_id=record_id,
            owner=owner,
            status=CeleryTaskStatus.FAILED,
            error_type=type(exc).__name__[:128],
            error_message="task execution failed; inspect redacted worker logs",
        )
        if not updated:
            raise RuntimeError(
                "Celery task failure state write rejected by owner fencing"
            ) from exc


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
