from __future__ import annotations

from collections.abc import Iterable

from internal.core import AppException, errors
from internal.schemas.agent import (
    AgentRunEventContextDTO,
    AgentRunResultDTO,
    AgentStepDTO,
    AgentStreamEventDTO,
)
from pkg.agents import AgentRunEvent, AgentRunEventType
from pkg.api_response import AppError


def managed_stream_event_from_run_event(
    event: AgentRunEvent, *, context: AgentRunEventContextDTO
) -> AgentStreamEventDTO:
    """把受管理运行的通用事件转换为带运行上下文的业务流式事件。

    受管理路径的事件都会带上 run_id、session_id、attempt_no 和 checkpoint_revision；
    暂停以 `run_interrupted` 结束本次流，不会发送 `run_completed`。
    """
    if event.type is AgentRunEventType.RUN_STARTED:
        stream_event = AgentStreamEventDTO.run_started(
            run_id=event.run_id,
            status=event.status.value,
            route=context.route,
        )
    elif event.type is AgentRunEventType.RUN_RESUMED:
        stream_event = AgentStreamEventDTO.run_resumed(
            run_id=event.run_id,
            status=event.status.value,
            route=context.route,
        )
    elif event.type is AgentRunEventType.STEP_COMPLETED:
        if event.step is None:
            raise RuntimeError("step_completed event missing step")
        stream_event = AgentStreamEventDTO.step_completed(
            run_id=event.run_id,
            step=AgentStepDTO.from_step_record(event.step),
            route=context.route,
        )
    elif event.type is AgentRunEventType.RUN_INTERRUPTED:
        stream_event = AgentStreamEventDTO.run_interrupted(
            run_id=event.run_id,
            status=event.status.value,
            stop_reason=event.stop_reason,
            route=context.route,
        )
    elif event.type is AgentRunEventType.RUN_COMPLETED:
        if event.result is None:
            raise RuntimeError("run_completed event missing result")
        stream_event = AgentStreamEventDTO.run_completed(
            result=AgentRunResultDTO.from_agent_result(
                event.result,
                session_id=context.session_id,
            ),
            route=context.route,
        )
    else:  # pragma: no cover - 防御分支
        raise RuntimeError(f"unsupported agent run event: {event.type}")

    return stream_event.with_context(context)


def stream_event_from_run_event(event: AgentRunEvent) -> AgentStreamEventDTO:
    """把通用 Agent runner 事件转换为业务流式事件。"""
    if event.type is AgentRunEventType.RUN_STARTED:
        return AgentStreamEventDTO.run_started(
            run_id=event.run_id,
            status=event.status.value,
        )

    if event.type is AgentRunEventType.STEP_COMPLETED:
        if event.step is None:
            raise RuntimeError("step_completed event missing step")
        return AgentStreamEventDTO.step_completed(
            run_id=event.run_id,
            step=AgentStepDTO.from_step_record(event.step),
        )

    if event.type is AgentRunEventType.RUN_COMPLETED:
        if event.result is None:
            raise RuntimeError("run_completed event missing result")
        return AgentStreamEventDTO.run_completed(
            result=AgentRunResultDTO.from_agent_result(event.result),
        )

    raise RuntimeError(f"unsupported agent run event: {event.type}")


def stream_events_from_result(
    result: AgentRunResultDTO,
) -> Iterable[AgentStreamEventDTO]:
    """把一次性 Agent 结果转换为流式事件序列。"""
    yield AgentStreamEventDTO.run_started(run_id=result.run_id)
    for step in result.steps:
        yield AgentStreamEventDTO.step_completed(run_id=result.run_id, step=step)
    yield AgentStreamEventDTO.run_completed(result=result)


def app_exception_stream_event(
    exc: AppException, *, run_id: str | None = None, route: str | None = None
) -> AgentStreamEventDTO:
    """把业务异常转换为稳定错误码的流式事件。"""
    return AgentStreamEventDTO.error(
        code=exc.error.code,
        message=_app_error_message(error=exc.error, detail=exc.message),
        run_id=run_id,
        route=route,
    )


def service_unavailable_stream_event(
    *,
    detail: str,
    run_id: str | None = None,
    route: str | None = None,
) -> AgentStreamEventDTO:
    """构造服务不可用流式事件。"""
    return AgentStreamEventDTO.error(
        code=errors.ServiceUnavailable.code,
        message=_app_error_message(error=errors.ServiceUnavailable, detail=detail),
        run_id=run_id,
        route=route,
    )


def _app_error_message(*, error: AppError, detail: str | None = None) -> str:
    base_message = error.get_message("zh")
    return f"{base_message}: {detail}" if detail else base_message
