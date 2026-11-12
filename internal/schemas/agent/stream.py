"""Agent SSE 事件与运行上下文 DTO。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from internal.schemas.agent.chat import (
    AgentRunResultDTO,
    AgentStepDTO,
)
from internal.schemas.agent.common import (
    JsonValue,
    _with_optional_fields,
    to_json_object,
)


class AgentStreamEventName(StrEnum):
    """Agent SSE 事件名。"""

    ROUTE = "route"
    RUN_STARTED = "run_started"
    RUN_RESUMED = "run_resumed"
    STEP_COMPLETED = "step_completed"
    RUN_INTERRUPTED = "run_interrupted"
    RUN_STATUS = "run_status"
    RUN_COMPLETED = "run_completed"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AgentRunEventContextDTO:
    """受管理运行事件的公共上下文，随每个运行事件一起下发。"""

    run_id: str
    session_id: str
    attempt_no: int
    checkpoint_revision: int | None = None
    route: str | None = None

    def to_fields(self) -> dict[str, JsonValue]:
        """转换为事件 data 中的公共字段。"""
        fields: dict[str, JsonValue] = {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "attempt_no": self.attempt_no,
        }
        if self.route is not None:
            fields["route"] = self.route
        if self.checkpoint_revision is not None:
            fields["checkpoint_revision"] = self.checkpoint_revision
        return fields


@dataclass(frozen=True, slots=True)
class AgentStreamEventDTO:
    """Service 输出给 Controller 的 Agent 流式事件。"""

    event: AgentStreamEventName
    data: dict[str, JsonValue] = field(default_factory=dict)
    result: AgentRunResultDTO | None = field(default=None, compare=False, repr=False)

    @classmethod
    def route(cls, *, route: str) -> AgentStreamEventDTO:
        """构造路由选择事件。"""
        return cls(event=AgentStreamEventName.ROUTE, data={"route": route})

    @classmethod
    def run_started(
        cls,
        *,
        run_id: str,
        status: str = "running",
        route: str | None = None,
    ) -> AgentStreamEventDTO:
        """构造 Agent 运行开始事件。"""
        return cls(
            event=AgentStreamEventName.RUN_STARTED,
            data=_with_optional_fields(
                {"run_id": run_id, "status": status},
                route=route,
            ),
        )

    @classmethod
    def run_resumed(
        cls,
        *,
        run_id: str,
        status: str = "running",
        route: str | None = None,
    ) -> AgentStreamEventDTO:
        """构造 Agent 运行恢复事件。"""
        return cls(
            event=AgentStreamEventName.RUN_RESUMED,
            data=_with_optional_fields(
                {"run_id": run_id, "status": status},
                route=route,
            ),
        )

    @classmethod
    def run_interrupted(
        cls,
        *,
        run_id: str,
        status: str = "interrupted",
        stop_reason: str | None = None,
        route: str | None = None,
    ) -> AgentStreamEventDTO:
        """构造 Agent 运行暂停事件；暂停不使用 run_completed 收尾。"""
        return cls(
            event=AgentStreamEventName.RUN_INTERRUPTED,
            data=_with_optional_fields(
                {"run_id": run_id, "status": status},
                route=route,
                stop_reason=stop_reason,
            ),
        )

    @classmethod
    def run_status(cls, *, data: dict[str, JsonValue]) -> AgentStreamEventDTO:
        """构造运行状态事件；重复 resume 时用它输出当前状态。"""
        return cls(event=AgentStreamEventName.RUN_STATUS, data=data)

    def with_context(self, context: AgentRunEventContextDTO) -> AgentStreamEventDTO:
        """把受管理运行上下文合并进事件 data。"""
        data = dict(self.data)
        data.update(context.to_fields())
        return AgentStreamEventDTO(event=self.event, data=data, result=self.result)

    @classmethod
    def step_completed(
        cls,
        *,
        run_id: str,
        step: AgentStepDTO,
        route: str | None = None,
    ) -> AgentStreamEventDTO:
        """构造单步完成事件。"""
        return cls(
            event=AgentStreamEventName.STEP_COMPLETED,
            data=_with_optional_fields(
                {
                    "run_id": run_id,
                    "step": to_json_object(step.to_schema().model_dump(mode="json")),
                },
                route=route,
            ),
        )

    @classmethod
    def run_completed(
        cls,
        *,
        result: AgentRunResultDTO,
        route: str | None = None,
    ) -> AgentStreamEventDTO:
        """构造 Agent 运行完成事件。"""
        return cls(
            event=AgentStreamEventName.RUN_COMPLETED,
            data=_with_optional_fields(
                {
                    "run_id": result.run_id,
                    "status": result.status,
                    "result": to_json_object(
                        result.to_schema().model_dump(mode="json")
                    ),
                },
                route=route,
            ),
            result=result,
        )

    @classmethod
    def error(
        cls,
        *,
        code: int,
        message: str,
        run_id: str | None = None,
        route: str | None = None,
    ) -> AgentStreamEventDTO:
        """构造 Agent 流式错误事件。"""
        return cls(
            event=AgentStreamEventName.ERROR,
            data=_with_optional_fields(
                {"code": code, "message": message},
                run_id=run_id,
                route=route,
            ),
        )

    def with_route(self, route: str) -> AgentStreamEventDTO:
        """返回带业务路由字段的新事件。"""
        data = dict(self.data)
        data["route"] = route
        return AgentStreamEventDTO(event=self.event, data=data, result=self.result)
