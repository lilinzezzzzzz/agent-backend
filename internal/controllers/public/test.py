import anyio
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from internal.core import AppException, errors
from internal.schemas import BaseResponse
from internal.utils.background_tasks import background_task_manager
from internal.utils.stream import stream_with_chunk_control
from pkg.logger import logger
from pkg.toolkit.response import ResponsePayload, success_response

router = APIRouter(prefix="/test", tags=["public test"])


# ==================== Schema ====================
class TestValidationRequest(BaseModel):
    """测试请求验证的 Schema"""

    name: str = Field(..., min_length=2, max_length=20, description="名称，2-20个字符")
    age: int = Field(..., ge=0, le=150, description="年龄，0-150")
    email: str = Field(..., pattern=r"^[\w\.-]+@[\w\.-]+\.\w+$", description="邮箱格式")


# ==================== 测试接口 ====================
@router.post(
    "/test_validation_error",
    response_model=BaseResponse[TestValidationRequest],
    summary="测试请求验证异常",
)
async def test_validation_error(req: TestValidationRequest) -> ResponsePayload:
    """
    测试 RequestValidationError 在中间件中的处理。

    传入不符合规则的数据会触发验证异常，例如:
    - name 长度不在 2-20 之间
    - age 不在 0-150 之间
    - email 格式不正确
    """
    return success_response(data=req.model_dump())


@router.get("/test_raise_exception", summary="测试异常")
async def test_raise_exception(_: Request):
    # 如果触发 fastapi.HTTPException 会优先被 FastAPI exception handler 捕获，
    # 如果是Exception会被middleware的exception.py捕获
    raise Exception("test_raise_exception")


@router.get("/test_raise_app_exception", summary="测试APP异常")
async def test_raise_app_exception():
    raise AppException(errors.InternalServerError, message="test_raise_app_exception")


async def async_task():
    """可以继承上下文的trace_id"""
    logger.info("async_task_trace_id-test")
    await anyio.sleep(10)


@router.get(
    "/test_contextvars_on_asyncio_task",
    response_model=BaseResponse[None],
    summary="测试Contextvars在Asyncio任务",
)
async def test_contextvars_on_asyncio_task() -> ResponsePayload:
    await background_task_manager.add_task("test", coro_func=async_task)
    return success_response()


async def text_generator():
    """异步生成器：逐字返回文本"""
    answer_text = "演示用异步生成器陆续返回文本答案。"
    for c in answer_text:
        yield c
        await anyio.sleep(0.05)


@router.get("/test/sse-stream", summary="测试SSE")
async def test_sse():
    async def event_generator():
        # 1. 请求进入，先返回：hello，正在查询资料
        yield "data: hello，正在查询资料\n\n"
        await anyio.sleep(2)

        # 2. sleep，返回：正在组织回答
        yield "data: 正在组织回答\n\n"
        await anyio.sleep(2)

        yield "data: 开始回答\n\n"
        yield "data: =========\n\n"
        # 3. 逐字返回文本答案
        answer_text = "演示了SSE的基本用法, 逐字返回文本答案"
        for c in answer_text:
            yield f"data: {c}\n\n"
            await anyio.sleep(0.05)

        yield "data: =========\n\n"

        # 3. sleep，用异步生成器陆续返回文本答案
        async for char in text_generator():
            yield f"data: {char}\n\n"

        yield "data: =========\n\n"
        yield "data: 回答结束\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


async def fake_stream_generator():
    for i in range(5):
        yield f"data: chunk {i}\n\n"
        await anyio.sleep(3)


@router.get("/chat/sse-stream/timeout", summary="测试SSE超时控制")
async def chat_endpoint(request: Request):
    wrapped_generator = stream_with_chunk_control(generator=fake_stream_generator(), chunk_timeout=2.0, is_sse=True)
    return StreamingResponse(wrapped_generator, media_type="text/event-stream")
