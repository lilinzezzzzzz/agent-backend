from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends

from internal.config import settings
from internal.core import AppException, errors
from internal.schemas import BaseResponse
from internal.schemas.celery_task import (
    CeleryTaskDetailSchema,
    CeleryTaskDispatchRespSchema,
    IdempotentSumCreateReqSchema,
)
from internal.services.celery_task import (
    CeleryTaskService,
    new_celery_task_service,
)
from internal.tasks.constants import (
    IDEMPOTENT_SUM_QUEUE,
    IDEMPOTENT_SUM_TASK_NAME,
    IDEMPOTENT_SUM_TIMEOUT_SECONDS,
)
from pkg.api_response import ResponsePayload, success_response
from pkg.request_context import get_trace_id, get_user_id


router = APIRouter(prefix="/celery-tasks", tags=["api celery tasks"])

CeleryTaskServiceDep = Annotated[CeleryTaskService, Depends(new_celery_task_service)]


@router.post(
    "/idempotent-sum/create",
    response_model=BaseResponse[CeleryTaskDispatchRespSchema],
    summary="创建幂等 Celery 加法任务",
)
async def create_idempotent_sum_task(
    req: IdempotentSumCreateReqSchema,
    service: CeleryTaskServiceDep,
) -> ResponsePayload:
    """创建幂等 Celery 加法任务。

    业务摘要:
        使用当前用户作用域和客户端幂等键登记一个异步加法 demo 任务。

    权限边界:
        需要有效用户 token；任务 scope 由服务端根据当前 user_id 生成，客户端不能覆盖。

    业务边界:
        PostgreSQL 事务先登记 task record，提交后由 API 同步调用 `CeleryClient.submit()`。
        相同 scope、task name、idempotency key 与 payload 返回原任务；不同 payload 返回
        `errors.IdempotencyConflict`。DB 与 broker 不是原子提交；发布失败或超时由状态机收敛为
        `PUBLISH_FAILED`，不会自动重投或重试。

    Args:
        req: 请求体，包含幂等键和两个待相加整数。
        service: 通过依赖注入获取的任务 Service。

    Returns:
        `BaseResponse[CeleryTaskDispatchRespSchema]`：返回 record ID、稳定 task ID、状态和
        created 标识；幂等载荷冲突返回 `errors.IdempotencyConflict`。
    """
    if not settings.CELERY_IDEMPOTENCY_ENABLED:
        raise AppException(
            errors.ServiceUnavailable, message="Celery idempotency is disabled"
        )
    result = await service.submit_once(
        task_name=IDEMPOTENT_SUM_TASK_NAME,
        trace_id=get_trace_id(),
        scope=f"user:{get_user_id()}",
        idempotency_key=req.idempotency_key,
        args=(req.x, req.y),
        queue=IDEMPOTENT_SUM_QUEUE,
        execution_timeout_seconds=IDEMPOTENT_SUM_TIMEOUT_SECONDS,
        idempotency_expires_in=timedelta(days=settings.CELERY_TASK_IDEMPOTENCY_DAYS),
    )
    return success_response(data=result.to_schema())


@router.get(
    "/{record_id}",
    response_model=BaseResponse[CeleryTaskDetailSchema],
    summary="查询 Celery 逻辑任务",
)
async def get_celery_task(
    record_id: int,
    service: CeleryTaskServiceDep,
) -> ResponsePayload:
    """查询 Celery 逻辑任务。

    业务摘要:
        按 record ID 查询当前用户作用域内的任务状态和脱敏错误摘要。

    权限边界:
        需要有效用户 token；只能读取 `user:{current_user_id}` scope 中的记录。

    业务边界:
        只读 PostgreSQL 任务事实源，不读 Celery result backend，不刷新、重投或取消任务。

    Args:
        record_id: 逻辑任务数据库 ID，路径参数。
        service: 通过依赖注入获取的任务 Service。

    Returns:
        `BaseResponse[CeleryTaskDetailSchema]`：返回任务状态、trace 和错误摘要；
        记录不存在或不属于当前 scope 返回 `errors.NotFound`。
    """
    result = await service.get_task(record_id=record_id, scope=f"user:{get_user_id()}")
    return success_response(data=result.to_schema())
