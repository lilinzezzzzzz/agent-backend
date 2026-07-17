"""Logger span 模拟接口。

用于演示 `pkg.logger.span` 在一次请求内产生的多层 span，以及后续把这些 span
持久化到数据库、供 OTEL 风格数据可视化消费的对外契约。当前阶段只覆盖内存中
的 span 采集，不涉及持久化。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from internal.schemas import BaseResponse
from internal.schemas.logger_span import (
    SpanSimulateReqSchema,
    SpanSimulateRespSchema,
)
from internal.services.logger_span import (
    LoggerSpanService,
    new_logger_span_service,
)
from pkg.api_response import ResponsePayload, success_response

router = APIRouter(prefix="/logger-span", tags=["api logger span"])

LoggerSpanServiceDep = Annotated[LoggerSpanService, Depends(new_logger_span_service)]


@router.post(
    "/simulate",
    response_model=BaseResponse[SpanSimulateRespSchema],
    summary="模拟 Logger span 请求链路",
)
async def simulate_logger_span(
    req: SpanSimulateReqSchema,
    service: LoggerSpanServiceDep,
) -> ResponsePayload:
    """模拟一次带多层 span 的请求链路，返回采集到的 span 结构。

    业务摘要:
        触发一次 `simulate.root -> validate -> db.read -> llm.intent -> rag.recall
        -> llm.business -> cache.write` 的嵌套 span 演示流程，返回包含真实
        `span_id` / `parent_span_id` 的节点，用于时间线和调用树可视化。

    权限边界:
        需要有效的用户 token（`/v1` 前缀默认认证）；接口不区分用户维度，
        所有已认证用户可见相同的模拟结果。

    业务边界:
        纯 CPU + 短 sleep 的内存演示，不访问数据库、缓存或外部服务；
        `fail_at` 命中时仅在 Service 内部触发一个受控异常，异常在 Service
        最外层被捕获，不会向 Controller 冒泡，也不会返回错误码。
        当前阶段结果仅写入进程内 LRU，未持久化到 DB。

    Args:
        req: 请求体。`scenario` 用于日志与响应回显；`fail_at` 可选，命中
            支持的步骤名（`validate` / `db.read` / `llm.intent` /
            `rag.recall` / `llm.business` / `cache.write`）会在对应 span
            内注入模拟异常，未命中的取值会被忽略并按成功路径执行。
        service: 通过依赖注入获取的 `LoggerSpanService` 实例。

    Returns:
        `BaseResponse[SpanSimulateRespSchema]`：成功时返回 scenario、
        `trace_id`、`root_span_id`、总耗时和 span 列表；
        `failed_step` 反映实际触发的模拟异常步骤，成功路径为 null。
    """
    result = await service.simulate_trace(
        scenario=req.scenario,
        fail_at=req.fail_at,
    )
    return success_response(data=result.to_schema())


@router.get(
    "/traces/{trace_id}",
    response_model=BaseResponse[SpanSimulateRespSchema],
    summary="查询 Logger span trace",
)
async def get_logger_span_trace(
    service: LoggerSpanServiceDep,
    trace_id: Annotated[
        str,
        Path(
            min_length=32,
            max_length=32,
            pattern=r"^[0-9a-fA-F]{32}$",
            description="32 位十六进制 trace_id，命中进程内缓存时返回真实采集结果。",
        ),
    ],
) -> ResponsePayload:
    """按 trace_id 读取一个完整的 span 采集结果。

    业务摘要:
        优先返回 `simulate_trace` 写入的真实 trace；未命中时基于 trace_id 的
        确定性哈希合成 mock span 数据，供前端可视化使用。

    权限边界:
        需要有效的用户 token（`/v1` 前缀默认认证）；trace store 不区分用户维度，
        任何已认证用户可以读取进程内任意 trace；当前阶段不适合导入真实业务数据。

    业务边界:
        只读接口，不修改进程内 LRU 缓存以外的 state，不访问数据库、Redis
        或外部服务；trace_id 会在传输层和 Service 层分别校验，后续持久化完成后
        本接口将改为从数据库读取。

    Args:
        service: 通过依赖注入获取的 `LoggerSpanService` 实例。
        trace_id: 目标 trace_id，路径参数，必须是 32 位十六进制字符串。

    Returns:
        `BaseResponse[SpanSimulateRespSchema]`：命中时返回真实 trace；未命中时
        返回确定性 mock 结果（scenario 以 `mock-` 开头）。
    """
    result = await service.get_trace(trace_id=trace_id)
    return success_response(data=result.to_schema())
