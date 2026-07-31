from fastapi import APIRouter

from internal.dependencies.rag import RagServiceDep
from internal.schemas import BaseResponse
from internal.schemas.rag import RagAnswerReqSchema, RagAnswerRespSchema
from pkg.api_response import ResponsePayload, success_response
from pkg.request_context import get_user_id

router = APIRouter(prefix="/rag", tags=["api rag"])


@router.post(
    "/answer",
    response_model=BaseResponse[RagAnswerRespSchema],
    summary="RAG 知识问答",
)
async def answer_rag_question(
    req: RagAnswerReqSchema,
    rag_service: RagServiceDep,
) -> ResponsePayload:
    """执行用户态 RAG 知识问答。

    业务摘要:
        根据用户问题和客户端期望检索范围执行受控知识库检索、生成回答并返回引用。

    权限边界:
        需要有效的用户 token（`/v1` 前缀默认认证）；客户端传入的 requested scope
        只作为期望范围，最终检索范围由 Service 使用服务端 allowed scope 求交得到。

    业务边界:
        只读接口，不写入业务数据；会调用 embedding/vector backend 和 LLM provider。
        不接收 tenant、gold answer、expected evidence 或评分参数。

    Args:
        req: 请求体，包含问题、requested scope、检索参数和 citation 要求。
        rag_service: 通过依赖注入获取的 `RagService` 实例。

    Returns:
        `BaseResponse[RagAnswerRespSchema]`：成功时返回 RAG run、回答、引用和 trace；
        无证据或空 scope 返回低置信度回答，LLM/vector provider 不可用时返回
        `errors.ServiceUnavailable`。
    """
    result = await rag_service.answer(
        user_id=get_user_id(),
        question=req.question,
        requested_domain=req.requested_domain,
        requested_kb_ids=req.requested_kb_ids,
        top_k=req.retrieval.top_k,
        final_k=req.retrieval.final_k,
        retrieval_mode=req.retrieval.mode,
        require_citations=req.require_citations,
    )
    return success_response(data=result.to_schema())
