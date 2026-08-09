"""Logger span 模拟接口。

用于演示 `pkg.tracing.span` 在一次请求内产生的多层 span。启用 OTLP Exporter 时，
Span 会由 tracing runtime 异步上报到 Collector；模拟接口只返回 trace_id，
不直接查询可观测性存储。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from internal.schemas import BaseResponse
from internal.schemas.logger_span import (
    SpanSimulateReqSchema,
    SpanSimulateRespSchema,
    SpanSimulateResultSchema,
)
from internal.services.logger_span import LoggerSpanService, new_logger_span_service
from pkg.api_response import ResponsePayload, success_response

router = APIRouter(prefix="/logger-span", tags=["api logger span"])


@router.post(
    "/simulate",
    response_model=BaseResponse[SpanSimulateResultSchema],
    summary="模拟 Logger span 请求链路",
)
async def simulate_logger_span(
    req: SpanSimulateReqSchema,
    service: Annotated[LoggerSpanService, Depends(new_logger_span_service)],
) -> ResponsePayload:
    """模拟一次带多层 span 的请求链路，返回 trace_id。

    业务摘要:
        触发一次 `simulate.root -> validate -> db.read -> llm.intent -> rag.recall
        -> llm.business -> cache.write` 的嵌套 span 演示流程，返回该链路的
        真实 `trace_id`，用于在可观测性存储中关联查询。

    权限边界:
        需要有效的用户 token（`/v1` 前缀默认认证）；接口不区分用户维度，
        所有已认证用户可见相同的模拟结果。

    业务边界:
        模拟步骤仅执行 CPU + 短 sleep，不访问业务数据库或缓存；
        `fail_at` 命中时仅在 Service 内部触发一个受控异常，异常在 Service
        最外层被捕获，不会向 Controller 冒泡，也不会返回错误码。
        真实 OTel Span 由 runtime 异步上报；接口不保留进程内 Span 明细，
        也不等待 Collector 接收或 ClickHouse 持久化完成。

    Args:
        req: 请求体。`scenario` 用于日志记录；`fail_at` 可选，命中
            支持的步骤名（`validate` / `db.read` / `llm.intent` /
            `rag.recall` / `llm.business` / `cache.write`）会在对应 span
            内注入模拟异常，未命中的取值会被忽略并按成功路径执行。
        service: 通过依赖注入获取的 `LoggerSpanService` 实例。

    Returns:
        `BaseResponse[SpanSimulateResultSchema]`：成功时只返回 `trace_id`；
        tracing 未启用时返回 `errors.ServiceUnavailable`。
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
    service: Annotated[LoggerSpanService, Depends(new_logger_span_service)],
    trace_id: Annotated[
        str,
        Path(
            min_length=32,
            max_length=32,
            pattern=r"^[0-9a-fA-F]{32}$",
            description="32 位十六进制 trace_id，用于生成确定性 mock Trace。",
        ),
    ],
) -> ResponsePayload:
    """按 trace_id 读取一个完整的 span 采集结果。

    业务摘要:
        根据 trace_id 的确定性哈希生成 mock Span 数据，供前端可视化使用。

    权限边界:
        需要有效的用户 token（`/v1` 前缀默认认证）；mock 数据不包含真实业务信息。

    业务边界:
        只读接口，不访问数据库、Redis 或外部服务，也不读取 `/simulate`
        产生的真实 Span；trace_id 会在传输层和 Service 层分别校验。

    Args:
        service: 通过依赖注入获取的 `LoggerSpanService` 实例。
        trace_id: 目标 trace_id，路径参数，必须是 32 位十六进制字符串。

    Returns:
        `BaseResponse[SpanSimulateRespSchema]`：返回确定性 mock Trace 数据，
        `scenario` 以 `mock-` 开头。
    """
    result = await service.get_trace(trace_id=trace_id)
    return success_response(data=result.to_schema())
