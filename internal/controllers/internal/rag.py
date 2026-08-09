from typing import Annotated

from fastapi import APIRouter, Depends

from internal.schemas import BaseResponse
from internal.schemas.rag import RagExternalRunReqSchema, RagExternalRunRespSchema
from internal.services.rag import RagService, new_rag_service
from pkg.api_response import ResponsePayload, success_response

router = APIRouter(prefix="/rag", tags=["internal rag"])


@router.post(
    "/external-runs",
    response_model=BaseResponse[RagExternalRunRespSchema],
    summary="生成 RAG 外部运行数据",
)
async def create_rag_external_run(
    req: RagExternalRunReqSchema,
    rag_service: Annotated[RagService, Depends(new_rag_service)],
) -> ResponsePayload:
    """运行单个外部 RAG case 并返回运行数据。

    业务摘要:
        代表 subject user 执行 RAG 链路，并返回外部调用方需要的运行数据。

    权限边界:
        需要 `/v1/internal` 内部签名认证；即使是内部接口，也必须通过 subject_user_id
        解析服务端 allowed scope，不能用 requested scope 绕过范围控制。

    业务边界:
        只执行 RAG 链路和运行数据组装，不接收 expected answer、expected chunk IDs、
        评分 mode 或 persist 参数；不执行评分动作、不计算指标、不判断 pass/fail。

    Args:
        req: 请求体，包含外部 case_id、subject_user_id、问题、requested scope 和检索参数。
        rag_service: 通过依赖注入获取的 `RagService` 实例。

    Returns:
        `BaseResponse[RagExternalRunRespSchema]`：返回本次 RAG 运行数据、证据映射、
        latency 和稳定错误原因。
    """
    result = await rag_service.run_external(
        case_id=req.case_id,
        subject_user_id=req.subject_user_id,
        question=req.question,
        requested_domain=req.requested_domain,
        requested_kb_ids=req.requested_kb_ids,
        top_k=req.retrieval.top_k,
        final_k=req.retrieval.final_k,
        retrieval_mode=req.retrieval.mode,
        return_context=req.return_context,
    )
    return success_response(data=result.to_schema())
