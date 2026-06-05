"""结构化 ReAct 执行循环：支持有限步工具调用和运行状态记录。

最小使用方式：

1. 定义一个或多个 `StructuredTool`，每个工具提供名称、描述、参数 schema 和
   handler。handler 接收 `Mapping[str, Any]`，可以是同步函数或异步函数。
2. 定义一个 action maker 对象，实现 `make_next_action(context)`。action maker 每轮
   都会收到用户输入、当前运行状态和工具列表，然后返回：
   - `AgentToolCall(tool="...", args={...})`：继续调用工具；
   - `AgentFinal(answer="...")`：结束循环并返回最终答案。
3. 创建 `ReActAgent(action_maker=..., tools=[...], max_steps=...)`，然后调用
   `await agent.run(user_input="...")`。

示例：

```python
from pkg.agents import (
    AgentActionContext,
    AgentFinal,
    AgentToolCall,
    ReActAgent,
    StructuredTool,
)


def search_order(args):
    return {"order_id": args["order_id"], "status": "shipped"}


class OrderActionMaker:
    async def make_next_action(self, context: AgentActionContext):
        if not context.state.steps:
            return AgentToolCall(tool="search_order", args={"order_id": "123"})

        action_result = context.state.steps[-1].action_result
        return AgentFinal(answer=f"查询结果：{action_result}")


agent = ReActAgent(
    action_maker=OrderActionMaker(),
    tools=[
        StructuredTool(
            name="search_order",
            description="按订单 ID 查询订单状态。",
            parameters_schema={
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
            handler=search_order,
        )
    ],
    max_steps=3,
)

result = await agent.run(user_input="查询订单 123")
```

接入真实 LLM 时，action maker 通常负责调用模型，并把模型返回的结构化 JSON
或 tool/function call 转成 `AgentToolCall` / `AgentFinal`。如果模型已经返回形如
`{"type": "tool_call", "tool": "...", "args": {...}}` 的字典，可以直接使用
`parse_agent_action()` 解析。
"""

from __future__ import annotations

from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Mapping,
    Sequence,
)
from dataclasses import dataclass, field
from enum import StrEnum
from inspect import isawaitable
from time import monotonic
from typing import Any, Protocol

from pkg.toolkit.string import uuid6_unique_str_id

ToolArgs = Mapping[str, Any]
ActionResult = Any
ToolHandler = Callable[[ToolArgs], ActionResult | Awaitable[ActionResult]]


class AgentRunStatus(StrEnum):
    """单次 Agent 运行的终态或当前状态。"""

    RUNNING = "running"
    COMPLETED = "completed"
    MAX_STEPS_REACHED = "max_steps_reached"


class AgentStepStatus(StrEnum):
    """单个 ReAct 步骤的执行状态。"""

    COMPLETED = "completed"
    FAILED = "failed"


class UnknownToolError(ValueError):
    """Action maker 请求了未注册工具时抛出。"""


class ToolExecutionError(RuntimeError):
    """工具执行失败且未启用错误捕获时抛出。"""


class InvalidAgentActionError(TypeError):
    """Action maker 返回了不受支持的动作对象时抛出。"""


@dataclass(frozen=True, slots=True)
class AgentToolCall:
    """模型给出的结构化工具调用动作。

    Attributes:
        tool: 要调用的工具名，必须匹配已注册的 `StructuredTool.name`。
        args: 传给工具的结构化参数，通常来自 LLM 的 tool/function call 参数。
        call_id: 可选调用 ID，用于和外部 LLM tool call ID 或 trace 记录关联。
    """

    tool: str
    args: ToolArgs = field(default_factory=dict)
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class AgentFinal:
    """模型给出的终止动作，表示本轮 Agent 可以直接返回最终答案。"""

    answer: str


AgentAction = AgentToolCall | AgentFinal


@dataclass(frozen=True, slots=True)
class StructuredTool:
    """暴露给 action maker 的结构化工具定义。

    `parameters_schema` 推荐使用 JSON Schema 形态，方便直接转给 OpenAI、
    Qwen 等支持 tool/function calling 的模型。`handler` 可以是同步函数，
    也可以是异步函数；运行器会统一 await 可能的异步结果。
    """

    name: str
    description: str
    parameters_schema: Mapping[str, Any]
    handler: ToolHandler
    is_readonly: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("tool name cannot be empty")
        if not self.description:
            raise ValueError("tool description cannot be empty")


@dataclass(slots=True)
class AgentStepRecord:
    """单个 ReAct 步骤的状态记录。

    该记录对应一次 Agent 动作。如果动作是 `AgentToolCall`，则会记录
    action_result、错误信息和耗时；如果动作是 `AgentFinal`，则只记录最终动作
    和该步耗时。
    """

    index: int
    action: AgentAction
    status: AgentStepStatus
    action_result: ActionResult | None = None
    error: str | None = None
    elapsed_ms: float = 0


@dataclass(slots=True)
class AgentRunState:
    """单次 Agent 运行期间维护的可变状态。

    Action maker 每一轮都会收到该对象，因此可以根据历史 `steps` 判断下一步应该
    继续调用工具、修正参数，还是返回 `AgentFinal`。
    """

    run_id: str
    user_input: str
    max_steps: int
    status: AgentRunStatus = AgentRunStatus.RUNNING
    steps: list[AgentStepRecord] = field(default_factory=list)
    final_answer: str | None = None


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """单次 Agent 运行结束后的不可变结果。"""

    run_id: str
    status: AgentRunStatus
    final_answer: str | None
    steps: Sequence[AgentStepRecord]


class AgentRunEventType(StrEnum):
    """Agent 运行事件类型。"""

    RUN_STARTED = "run_started"
    STEP_COMPLETED = "step_completed"
    RUN_COMPLETED = "run_completed"


@dataclass(frozen=True, slots=True)
class AgentRunEvent:
    """单次 Agent 运行期间产生的结构化事件。"""

    type: AgentRunEventType
    run_id: str
    status: AgentRunStatus
    step: AgentStepRecord | None = None
    result: AgentRunResult | None = None


@dataclass(frozen=True, slots=True)
class AgentActionContext:
    """Action maker 每轮选择动作需要的上下文。"""

    user_input: str
    state: AgentRunState
    tools: Sequence[StructuredTool]


class AgentActionMaker(Protocol):
    """Action maker 协议：把当前上下文转换成 Agent 下一步动作。

    实际业务通常会在 `make_next_action()` 内部调用 LLM，并把模型返回的 JSON 或
    tool call 转换为 `AgentToolCall` / `AgentFinal`。显式方法名比 `__call__`
    更容易表达“生成下一步动作”的语义。
    """

    async def make_next_action(self, context: AgentActionContext) -> AgentAction: ...


def parse_agent_action(payload: Mapping[str, Any]) -> AgentAction:
    """将结构化 LLM payload 解析为 Agent 动作。

    支持两种 payload：

    - `{"type": "final", "answer": "..."}`
    - `{"type": "tool_call", "tool": "...", "args": {...}}`

    该函数只做轻量结构校验，不按 `parameters_schema` 深度校验工具参数；
    业务侧如需严格校验，可以在 action maker 或工具 handler 内补充 Pydantic/JSON
    Schema 校验。
    """
    action_type = payload.get("type")
    if action_type == "final":
        answer = payload.get("answer")
        if not isinstance(answer, str):
            raise InvalidAgentActionError("final action requires string answer")
        return AgentFinal(answer=answer)

    if action_type == "tool_call":
        tool = payload.get("tool")
        args = payload.get("args", {})
        call_id = payload.get("call_id")
        if not isinstance(tool, str) or not tool:
            raise InvalidAgentActionError(
                "tool_call action requires non-empty string tool"
            )
        if not isinstance(args, Mapping):
            raise InvalidAgentActionError("tool_call action requires mapping args")
        if call_id is not None and not isinstance(call_id, str):
            raise InvalidAgentActionError("tool_call action call_id must be a string")
        return AgentToolCall(tool=tool, args=args, call_id=call_id)

    raise InvalidAgentActionError(f"unsupported action type: {action_type!r}")


class ReActAgent:
    """执行有限步结构化 ReAct 循环。

    循环形态为：

    1. 调用 action maker，让模型基于用户输入和历史状态产出下一步动作。
    2. 如果是 `AgentFinal`，记录最终答案并结束。
    3. 如果是 `AgentToolCall`，执行对应工具，把动作结果写入 action_result。
    4. 未拿到最终答案时继续下一轮，直到达到 `max_steps`。

    默认会把未知工具、非法参数和工具异常记录成 action_result，方便 action maker
    在下一轮根据错误信息自我修正；如果希望工具错误直接中断运行，可以把
    `capture_tool_errors` 设为 `False`。
    """

    def __init__(
        self,
        *,
        action_maker: AgentActionMaker,
        tools: Sequence[StructuredTool],
        max_steps: int = 8,
        capture_tool_errors: bool = True,
    ):
        # 有限循环是 Agent 的基础保护，避免模型在工具调用中无限自旋。
        if max_steps < 1:
            raise ValueError("max_steps must be greater than 0")

        # 工具名是 action maker 调用工具的唯一索引，重复会导致后注册工具覆盖前者。
        duplicate_tools = _find_duplicates(tool.name for tool in tools)
        if duplicate_tools:
            raise ValueError(f"duplicate tool names: {', '.join(duplicate_tools)}")

        self._action_maker = action_maker
        self._tools = {tool.name: tool for tool in tools}
        self._max_steps = max_steps
        self._capture_tool_errors = capture_tool_errors

    async def run(
        self, *, user_input: str, run_id: str | None = None
    ) -> AgentRunResult:
        """执行动作/工具迭代，直到得到最终答案或达到步数上限。"""
        result: AgentRunResult | None = None
        async for event in self.run_events(user_input=user_input, run_id=run_id):
            if event.type is AgentRunEventType.RUN_COMPLETED:
                result = event.result

        if result is None:
            raise RuntimeError("agent run completed without result event")
        return result

    async def run_events(
        self, *, user_input: str, run_id: str | None = None
    ) -> AsyncIterator[AgentRunEvent]:
        """执行动作/工具迭代，并在运行开始、每步完成和运行结束时产出事件。"""
        state = AgentRunState(
            run_id=run_id or uuid6_unique_str_id(),
            user_input=user_input,
            max_steps=self._max_steps,
        )
        yield AgentRunEvent(
            type=AgentRunEventType.RUN_STARTED,
            run_id=state.run_id,
            status=state.status,
        )

        for index in range(self._max_steps):
            started_at = monotonic()

            # 每一轮都把当前 state 传给 action maker，让它基于历史 action_result 选择动作。
            action = await self._action_maker.make_next_action(
                AgentActionContext(
                    user_input=user_input,
                    state=state,
                    tools=tuple(self._tools.values()),
                )
            )

            # `AgentFinal` 是正常结束路径：记录最终答案并立即返回不可变结果。
            if isinstance(action, AgentFinal):
                state.status = AgentRunStatus.COMPLETED
                state.final_answer = action.answer
                state.steps.append(
                    AgentStepRecord(
                        index=index,
                        action=action,
                        status=AgentStepStatus.COMPLETED,
                        elapsed_ms=_elapsed_ms(started_at),
                    )
                )
                yield AgentRunEvent(
                    type=AgentRunEventType.STEP_COMPLETED,
                    run_id=state.run_id,
                    status=state.status,
                    step=state.steps[-1],
                )
                result = _build_result(state)
                yield AgentRunEvent(
                    type=AgentRunEventType.RUN_COMPLETED,
                    run_id=state.run_id,
                    status=result.status,
                    result=result,
                )
                return

            # 除 `AgentFinal` 和 `AgentToolCall` 以外都是 action maker 编程错误。
            if not isinstance(action, AgentToolCall):
                raise InvalidAgentActionError(f"invalid agent action: {action!r}")

            # 工具返回值或错误都会写入状态，作为下一轮 action maker 的 action_result。
            action_result, error = await self._execute_tool_call(action)
            step_status = AgentStepStatus.FAILED if error else AgentStepStatus.COMPLETED
            state.steps.append(
                AgentStepRecord(
                    index=index,
                    action=action,
                    status=step_status,
                    action_result=action_result,
                    error=error,
                    elapsed_ms=_elapsed_ms(started_at),
                )
            )
            yield AgentRunEvent(
                type=AgentRunEventType.STEP_COMPLETED,
                run_id=state.run_id,
                status=state.status,
                step=state.steps[-1],
            )

        # 达到最大步数时不伪造答案，调用方可以根据 status 决定重试或降级回复。
        state.status = AgentRunStatus.MAX_STEPS_REACHED
        result = _build_result(state)
        yield AgentRunEvent(
            type=AgentRunEventType.RUN_COMPLETED,
            run_id=state.run_id,
            status=result.status,
            result=result,
        )

    async def _execute_tool_call(
        self, action: AgentToolCall
    ) -> tuple[ActionResult | None, str | None]:
        """执行一次工具调用，并按配置决定捕获错误还是抛出错误。"""
        if not isinstance(action.args, Mapping):
            error = "tool args must be a mapping"
            if self._capture_tool_errors:
                return {"error": error}, error
            raise ToolExecutionError(error)

        tool = self._tools.get(action.tool)
        if tool is None:
            error = f"unknown tool: {action.tool}"
            if self._capture_tool_errors:
                return {"error": error}, error
            raise UnknownToolError(error)

        try:
            result = tool.handler(action.args)
            return await _maybe_await(result), None
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if self._capture_tool_errors:
                return {"error": error}, error
            raise ToolExecutionError(error) from exc


async def _maybe_await[T](value: T | Awaitable[T]) -> T:
    """兼容同步值和 Awaitable，统一返回最终结果。"""
    if isawaitable(value):
        return await value
    return value


def _elapsed_ms(started_at: float) -> float:
    """计算从 `started_at` 到当前时刻的毫秒耗时。"""
    return round((monotonic() - started_at) * 1000, 3)


def _build_result(state: AgentRunState) -> AgentRunResult:
    """把内部可变运行状态转换成对外不可变运行结果。"""
    return AgentRunResult(
        run_id=state.run_id,
        status=state.status,
        final_answer=state.final_answer,
        steps=tuple(state.steps),
    )


def _find_duplicates(values: Iterable[str]) -> list[str]:
    """按首次重复出现的顺序返回重复值。"""
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen:
            duplicates.append(value)
        seen.add(value)
    return duplicates
