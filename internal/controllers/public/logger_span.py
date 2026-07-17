"""Logger span 可视化 public 接口。

- `GET /v1/public/logger-span/viewer` 返回根目录 `frontend/logger_span/index.html`
  内容，供浏览器直接打开可视化页面，不需要另启前端服务。
- `GET /v1/public/logger-span/traces/{trace_id}` 为上述页面提供无需登录的
  数据镜像，与 authenticated `GET /v1/logger-span/traces/{trace_id}` 共享同一
  Service，返回完全一致的 DTO。

当前阶段为 demo 用途，trace store 仅保存在进程内；后续持久化方案
落地后，本接口可以直接切换为从 DB 读取。
"""

from pathlib import Path as FsPath
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from fastapi.responses import HTMLResponse

from internal import BASE_DIR
from internal.schemas import BaseResponse
from internal.schemas.logger_span import SpanSimulateRespSchema
from internal.services.logger_span import (
    LoggerSpanService,
    new_logger_span_service,
)
from pkg.api_response import ResponsePayload, success_response

router = APIRouter(prefix="/logger-span", tags=["public logger span"])

LoggerSpanServiceDep = Annotated[LoggerSpanService, Depends(new_logger_span_service)]

_FRONTEND_INDEX_FILE: FsPath = BASE_DIR / "frontend" / "logger_span" / "index.html"


@router.get(
    "/traces/{trace_id}",
    response_model=BaseResponse[SpanSimulateRespSchema],
    summary="公开查询 Logger span trace（供 demo 可视化页面）",
)
async def get_logger_span_trace_public(
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
    """无需登录也可读取 trace 采集结果的 demo 镜像接口。

    业务摘要:
        与 authenticated `GET /v1/logger-span/traces/{trace_id}` 行为完全一致，
        供浏览器内可视化页面直接拉取 span 数据。

    权限边界:
        无需认证（`/v1/public` 前缀）；trace store 不区分用户维度，
        不适合导入真实业务数据；后续持久化完成后需重新评估可否保留开放。

    业务边界:
        只读接口，不修改进程内 LRU 缓存以外的 state，不访问数据库、Redis
        或外部服务；trace_id 会在传输层和 Service 层分别校验。

    Args:
        service: 通过依赖注入获取的 `LoggerSpanService` 实例。
        trace_id: 目标 trace_id，路径参数，必须是 32 位十六进制字符串。

    Returns:
        `BaseResponse[SpanSimulateRespSchema]`：命中时返回真实 trace；未命中时
        返回确定性 mock 结果。
    """
    result = await service.get_trace(trace_id=trace_id)
    return success_response(data=result.to_schema())


@router.get(
    "/viewer",
    response_class=HTMLResponse,
    summary="Logger span 可视化 demo 页面",
    include_in_schema=False,
)
async def logger_span_viewer() -> HTMLResponse:
    """返回仓库根目录下 `frontend/logger_span/index.html` 作为 demo 页面。

    业务摘要:
        提供一个无需单独前端服务即可打开的可视化页面，页面内
        自行调用 `/v1/public/logger-span/traces/{trace_id}` 拉取数据。

    权限边界:
        无需认证（`/v1/public` 前缀）。页面本身不包含敏感信息，
        数据接口仅返回属于 demo 范围的链路数据。

    业务边界:
        只读本地 HTML 文件并回写 body；不访问数据库、缓存、外部服务。
        HTML 文件缺失时返回一份内嵌的错误提示页面，不抛 HTTP 500，
        便于排查部署位置问题。

    Returns:
        `HTMLResponse`：直接渲染为 HTML 页面，不走 `BaseResponse` 信封。
    """
    if not _FRONTEND_INDEX_FILE.exists():
        message = (
            "logger span viewer HTML not found. "
            f"Expected file at {_FRONTEND_INDEX_FILE}."
        )
        return HTMLResponse(
            content=(
                '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                "<title>Logger Span Viewer</title></head><body>"
                f"<h1>Viewer asset missing</h1><pre>{message}</pre>"
                "</body></html>"
            ),
            status_code=500,
        )

    content = _FRONTEND_INDEX_FILE.read_text(encoding="utf-8")
    return HTMLResponse(content=content)
