"""结构化 ReAct 执行循环：支持有限步工具调用、运行状态记录和受管理恢复。

最小使用方式：

1. 定义一个或多个 `StructuredTool`，每个工具提供名称、描述、参数 schema、
   handler 和显式的恢复策略。handler 接收 `Mapping[str, Any]`，可以是同步函数或
   异步函数。
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
    ToolReplayPolicy,
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
            replay_policy=ToolReplayPolicy.REPLAY_SAFE,
        )
    ],
    max_steps=3,
)

result = await agent.run(user_input="查询订单 123")
```

接入真实 LLM 时，action maker 通常负责调用模型，并把模型返回的结构化 JSON
或 tool/final 载荷转成 `AgentToolCall` / `AgentFinal`。如果模型已经返回形如
`{"type": "tool_call", "tool": "...", "args": {...}}` 的字典，可以直接使用
`parse_agent_action()` 解析。

受管理运行通过 `AgentRunRuntime` 协议持久化执行现场：传入 `runtime` 时，执行器在每个
安全点提交 checkpoint，并可被 interrupt 暂停；`resume_events()` 只接受已校验的
`AgentCheckpoint`，不接受调用方替换原始问题、步骤或步数上限。未传 `runtime` 时行为与
纯内存执行完全一致。
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
from typing import TYPE_CHECKING, Any, Protocol

from pkg.agents.state import (
    AgentCheckpoint,
    AgentCheckpointStep,
    AgentRunPhase,
    CheckpointValidationError,
    ToolReplayPolicy,
    stable_tool_call_id,
)
from pkg.ids import uuid7_unique_str_id

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型检查，避免与 runtime 循环导入
    from pkg.agents.runtime import AgentRunRuntime

ToolArgs = Mapping[str, Any]
ActionResult = Any
ToolHandler = Callable[[ToolArgs], ActionResult | Awaitable[ActionResult]]

AGENT_TOOL_CALL_ID_ARG = "_agent_tool_call_id"
"""声明为 `idempotent` 的工具在重放时额外收到的稳定调用键参数名。

下游可以按该稳定键去重或查询既有结果；`replay_safe` / `non_replayable` 工具不会收到该参数。
"""


class AgentRunStatus(StrEnum):
    """单次 Agent 运行的终态或当前状态。"""

    RUNNING = "running"
    COMPLETED = "completed"
    MAX_STEPS_REACHED = "max_steps_reached"
    INTERRUPTED = "interrupted"
    RECOVERY_REQUIRED = "recovery_required"
    FAILED = "failed"


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


class UnsafeAgentResumeError(RuntimeError):
    """checkpoint 无法安全恢复时抛出，调用方必须转为 recovery_required。"""


@dataclass(frozen=True, slots=True)
class AgentToolCall:
    """模型给出的结构化工具调用动作。

    Attributes:
        tool: 要调用的工具名，必须匹配已注册的 `StructuredTool.name`。
        args: 传给工具的结构化参数，通常来自 LLM 的 tool/function call 参数。
        call_id: 可选调用 ID，用于和外部 LLM tool call ID 或 trace 记录关联。
            它不是幂等键；持久化标识由 `stable_tool_call_id()` 生成。
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

    `replay_policy` 必须显式声明：未声明的工具按 `non_replayable` 处理，恢复时不会
    自动重复调用，而是把 run 停在 `recovery_required`。
    """

    name: str
    description: str
    parameters_schema: Mapping[str, Any]
    handler: ToolHandler
    is_readonly: bool = True
    replay_policy: ToolReplayPolicy = ToolReplayPolicy.NON_REPLAYABLE

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
    RUN_RESUMED = "run_resumed"
    STEP_COMPLETED = "step_completed"
    RUN_INTERRUPTED = "run_interrupted"
    RUN_COMPLETED = "run_completed"


@dataclass(frozen=True, slots=True)
class AgentRunEvent:
    """单次 Agent 运行期间产生的结构化事件。

    `checkpoint_revision` 是该事件对应的已提交 checkpoint 版本；纯内存执行时为 None。
    只有已提交的状态才会产生事件，事件丢失不回滚已完成步骤。
    """

    type: AgentRunEventType
    run_id: str
    status: AgentRunStatus
    step: AgentStepRecord | None = None
    result: AgentRunResult | None = None
    checkpoint_revision: int | None = None
    stop_reason: str | None = None


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


class AgentResumeDecision(StrEnum):
    """checkpoint 的恢复判定结果。"""

    RESUME = "resume"
    RECOVERY_REQUIRED = "recovery_required"


@dataclass(frozen=True, slots=True)
class AgentResumeAssessment:
    """恢复前对 checkpoint 的安全判定。

    Attributes:
        decision: 允许继续恢复，还是必须停在 `recovery_required`。
        phase: checkpoint 记录的执行阶段。
        reason: 拒绝恢复时的诊断原因。
        tool: 涉及的待执行工具名。
        replay_policy: 该工具的恢复策略。
        replayed: 恢复时是否需要重新调用该工具。
    """

    decision: AgentResumeDecision
    phase: AgentRunPhase
    reason: str | None = None
    tool: str | None = None
    replay_policy: ToolReplayPolicy | None = None
    replayed: bool = False


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


def serialize_agent_action(
    action: AgentAction, *, tool_call_id: str | None = None
) -> dict[str, Any]:
    """把 Agent 动作序列化为 checkpoint 使用的 JSON object。

    `tool_call_id` 是服务端按 `run_id + step_index` 生成的稳定调用标识；它独立于 LLM
    返回的随机 `call_id`，恢复重放同一动作时保持不变。
    """
    if isinstance(action, AgentFinal):
        return {"type": "final", "answer": action.answer}
    payload: dict[str, Any] = {
        "type": "tool_call",
        "tool": action.tool,
        "args": dict(action.args),
        "call_id": action.call_id,
    }
    if tool_call_id is not None:
        payload["tool_call_id"] = tool_call_id
    return payload


def deserialize_agent_action(payload: Mapping[str, Any]) -> AgentAction:
    """把 checkpoint 中的动作载荷还原为 Agent 动作。"""
    return parse_agent_action(payload)


def action_tool_call_id(
    payload: Mapping[str, Any], *, run_id: str, step_index: int
) -> str:
    """读取已持久化的稳定工具调用标识；缺失时按 run_id 与步骤序号重新生成。"""
    stored = payload.get("tool_call_id")
    if isinstance(stored, str) and stored:
        return stored
    return stable_tool_call_id(run_id=run_id, step_index=step_index)


def step_record_to_checkpoint_step(
    step: AgentStepRecord, *, tool_call_id: str | None = None
) -> AgentCheckpointStep:
    """把运行时步骤记录转换为可持久化的 checkpoint 快照。"""
    return AgentCheckpointStep(
        index=step.index,
        status=step.status.value,
        action=serialize_agent_action(step.action, tool_call_id=tool_call_id),
        action_result=step.action_result,
        error=step.error,
        elapsed_ms=step.elapsed_ms,
    )


def checkpoint_step_to_record(step: AgentCheckpointStep) -> AgentStepRecord:
    """把 checkpoint 快照还原为运行时步骤记录。"""
    try:
        status = AgentStepStatus(step.status)
    except ValueError as exc:
        raise CheckpointValidationError(
            f"checkpoint step {step.index} has unsupported status: {step.status!r}"
        ) from exc
    return AgentStepRecord(
        index=step.index,
        action=deserialize_agent_action(step.action),
        status=status,
        action_result=step.action_result,
        error=step.error,
        elapsed_ms=step.elapsed_ms,
    )


def state_from_checkpoint(checkpoint: AgentCheckpoint) -> AgentRunState:
    """按 checkpoint 重建可变运行状态。"""
    state = AgentRunState(
        run_id=checkpoint.run_id,
        user_input=checkpoint.user_input,
        max_steps=checkpoint.max_steps,
        steps=[checkpoint_step_to_record(step) for step in checkpoint.steps],
    )
    state.final_answer = checkpoint.final_answer
    return state


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

    传入 `runtime` 时进入受管理模式：动作、工具和终态边界都会提交 checkpoint，
    并在提交时原子检查打断意图。受管理模式下必须等待存储确认后才能继续下一动作。
    """

    def __init__(
        self,
        *,
        action_maker: AgentActionMaker,
        tools: Sequence[StructuredTool],
        max_steps: int = 8,
        capture_tool_errors: bool = True,
        definition_version: str = "v1",
    ):
        # 有限循环是 Agent 的基础保护，避免模型在工具调用中无限自旋。
        if max_steps < 1:
            raise ValueError("max_steps must be greater than 0")
        if not definition_version:
            raise ValueError("definition_version cannot be empty")

        # 工具名是 action maker 调用工具的唯一索引，重复会导致后注册工具覆盖前者。
        duplicate_tools = _find_duplicates(tool.name for tool in tools)
        if duplicate_tools:
            raise ValueError(f"duplicate tool names: {', '.join(duplicate_tools)}")

        self._action_maker = action_maker
        self._tools = {tool.name: tool for tool in tools}
        self._max_steps = max_steps
        self._capture_tool_errors = capture_tool_errors
        self._definition_version = definition_version

    @property
    def definition_version(self) -> str:
        """当前执行器绑定的 Builder / prompt / 工具语义版本。"""
        return self._definition_version

    async def run(
        self,
        *,
        user_input: str,
        run_id: str | None = None,
        runtime: AgentRunRuntime | None = None,
    ) -> AgentRunResult:
        """执行动作/工具迭代，直到得到最终答案或达到步数上限。"""
        result: AgentRunResult | None = None
        async for event in self.run_events(
            user_input=user_input, run_id=run_id, runtime=runtime
        ):
            if event.type in (
                AgentRunEventType.RUN_COMPLETED,
                AgentRunEventType.RUN_INTERRUPTED,
            ):
                result = event.result

        if result is None:
            raise RuntimeError("agent run completed without result event")
        return result

    async def resume(
        self,
        *,
        checkpoint: AgentCheckpoint,
        runtime: AgentRunRuntime,
    ) -> AgentRunResult:
        """从已校验的 checkpoint 继续执行到暂停或终态。"""
        result: AgentRunResult | None = None
        async for event in self.resume_events(checkpoint=checkpoint, runtime=runtime):
            if event.type in (
                AgentRunEventType.RUN_COMPLETED,
                AgentRunEventType.RUN_INTERRUPTED,
            ):
                result = event.result

        if result is None:
            raise RuntimeError("agent resume finished without result event")
        return result

    async def run_events(
        self,
        *,
        user_input: str,
        run_id: str | None = None,
        runtime: AgentRunRuntime | None = None,
    ) -> AsyncIterator[AgentRunEvent]:
        """执行动作/工具迭代，并在运行开始、每步完成和运行结束时产出事件。

        传入 `runtime` 时，从 runtime 中已提交的初始 checkpoint 继续执行，第一次执行
        发出 `run_started`；不传 `runtime` 时保持纯内存行为。
        """
        state = AgentRunState(
            run_id=run_id or uuid7_unique_str_id(),
            user_input=user_input,
            max_steps=self._max_steps,
        )

        checkpoint: AgentCheckpoint | None = None
        if runtime is not None:
            checkpoint = self._require_checkpoint(await runtime.load_checkpoint())
            if checkpoint.run_id != state.run_id:
                raise CheckpointValidationError(
                    "checkpoint run_id does not match the requested run"
                )
            state = state_from_checkpoint(checkpoint)

        yield self._build_event(
            type=AgentRunEventType.RUN_STARTED,
            state=state,
            checkpoint=checkpoint,
        )

        async for event in self._drive(
            state=state,
            checkpoint=checkpoint,
            runtime=runtime,
        ):
            yield event

    async def resume_events(
        self,
        *,
        checkpoint: AgentCheckpoint,
        runtime: AgentRunRuntime,
    ) -> AsyncIterator[AgentRunEvent]:
        """从已校验的 checkpoint 恢复同一个 run。

        该方法不接受调用方替换原始问题、步骤或步数上限；恢复前会重新校验
        definition_version、工具存在性和工具重放策略。无法安全重放时抛出
        `UnsafeAgentResumeError`，调用方应把 run 停在 `recovery_required`。
        """
        assessment = self.assess_resume(checkpoint)
        if assessment.decision is AgentResumeDecision.RECOVERY_REQUIRED:
            raise UnsafeAgentResumeError(assessment.reason or "resume is not safe")

        state = state_from_checkpoint(checkpoint)
        yield self._build_event(
            type=AgentRunEventType.RUN_RESUMED,
            state=state,
            checkpoint=checkpoint,
        )

        async for event in self._drive(
            state=state,
            checkpoint=checkpoint,
            runtime=runtime,
        ):
            yield event

    def assess_resume(self, checkpoint: AgentCheckpoint) -> AgentResumeAssessment:
        """判定 checkpoint 是否可以安全恢复，以及是否需要重放工具。

        规则遵循设计文档：`routing` / `before_action` 允许重做未完成模型调用；
        `before_tool` 直接执行已保存动作；`tool_in_flight` 取决于工具声明的重放策略；
        `final_ready` 无需模型调用，直接完成终态提交。
        """
        if checkpoint.definition_version != self._definition_version:
            raise CheckpointValidationError(
                "checkpoint definition_version is not compatible with this agent"
            )

        for step in checkpoint.steps:
            if step.action_type == "tool_call" and step.tool not in self._tools:
                raise UnknownToolError(
                    f"checkpoint references unknown tool: {step.tool}"
                )

        phase = checkpoint.phase
        if phase in (AgentRunPhase.ROUTING, AgentRunPhase.BEFORE_ACTION):
            return AgentResumeAssessment(
                decision=AgentResumeDecision.RESUME, phase=phase
            )

        if phase is AgentRunPhase.FINAL_READY:
            if checkpoint.final_answer is None:
                raise CheckpointValidationError(
                    "final_ready checkpoint requires an answer"
                )
            return AgentResumeAssessment(
                decision=AgentResumeDecision.RESUME, phase=phase
            )

        pending = checkpoint.pending_action or {}
        tool_name = pending.get("tool")
        if not isinstance(tool_name, str) or not tool_name:
            raise CheckpointValidationError("pending_action requires a tool name")
        tool = self._tools.get(tool_name)
        if tool is None:
            raise UnknownToolError(f"checkpoint references unknown tool: {tool_name}")

        if phase is AgentRunPhase.BEFORE_TOOL:
            return AgentResumeAssessment(
                decision=AgentResumeDecision.RESUME,
                phase=phase,
                tool=tool_name,
                replay_policy=tool.replay_policy,
            )

        if tool.replay_policy is ToolReplayPolicy.NON_REPLAYABLE:
            return AgentResumeAssessment(
                decision=AgentResumeDecision.RECOVERY_REQUIRED,
                phase=phase,
                reason=(
                    f"tool {tool_name!r} was in flight and is declared non_replayable"
                ),
                tool=tool_name,
                replay_policy=tool.replay_policy,
            )

        return AgentResumeAssessment(
            decision=AgentResumeDecision.RESUME,
            phase=phase,
            tool=tool_name,
            replay_policy=tool.replay_policy,
            replayed=True,
        )

    async def _drive(
        self,
        *,
        state: AgentRunState,
        checkpoint: AgentCheckpoint | None,
        runtime: AgentRunRuntime | None,
    ) -> AsyncIterator[AgentRunEvent]:
        """从指定现场推进执行，直到终态或安全点暂停。"""
        phase = (
            checkpoint.phase if checkpoint is not None else AgentRunPhase.BEFORE_ACTION
        )
        # 路由由应用层在调用执行器之前完成；此处只把 routing 视为“尚未生成动作”。
        if phase is AgentRunPhase.ROUTING:
            phase = AgentRunPhase.BEFORE_ACTION

        index = len(state.steps)
        pending_action: AgentToolCall | None = None
        if checkpoint is not None and phase in (
            AgentRunPhase.BEFORE_TOOL,
            AgentRunPhase.TOOL_IN_FLIGHT,
        ):
            restored = deserialize_agent_action(checkpoint.pending_action or {})
            if not isinstance(
                restored, AgentToolCall
            ):  # pragma: no cover - codec 已校验
                raise CheckpointValidationError("pending_action must be a tool_call")
            pending_action = restored

        if phase is AgentRunPhase.FINAL_READY:
            final_step = AgentStepRecord(
                index=index,
                action=AgentFinal(answer=checkpoint.final_answer or ""),
                status=AgentStepStatus.COMPLETED,
                elapsed_ms=0,
            )
            state.steps.append(final_step)
            state.final_answer = checkpoint.final_answer
            state.status = AgentRunStatus.COMPLETED
            async for event in self._commit_final(
                state=state,
                checkpoint=checkpoint,
                runtime=runtime,
                step=final_step,
            ):
                yield event
            return

        started_at = monotonic()
        while index < state.max_steps:
            if phase is AgentRunPhase.BEFORE_ACTION:
                stop_reason = await self._control_stop_reason(runtime)
                if stop_reason is not None:
                    if runtime is not None:
                        checkpoint = await runtime.load_checkpoint()
                    state.status = AgentRunStatus.INTERRUPTED
                    yield self._build_event(
                        type=AgentRunEventType.RUN_INTERRUPTED,
                        state=state,
                        checkpoint=checkpoint,
                        result=_build_result(state),
                        stop_reason=stop_reason,
                    )
                    return

                # 每一轮都把当前 state 传给 action maker，让它基于历史 action_result 选择动作。
                action = await self._action_maker.make_next_action(
                    AgentActionContext(
                        user_input=state.user_input,
                        state=state,
                        tools=tuple(self._tools.values()),
                    )
                )

                # 除 `AgentFinal` 和 `AgentToolCall` 以外都是 action maker 编程错误。
                if not isinstance(action, AgentToolCall | AgentFinal):
                    raise InvalidAgentActionError(f"invalid agent action: {action!r}")

                if isinstance(action, AgentFinal):
                    final_step = AgentStepRecord(
                        index=index,
                        action=action,
                        status=AgentStepStatus.COMPLETED,
                        elapsed_ms=_elapsed_ms(started_at),
                    )
                    state.steps.append(final_step)
                    state.final_answer = action.answer
                    state.status = AgentRunStatus.COMPLETED
                    if runtime is not None:
                        # 受管理模式：final 先落库为 final_ready，终态提交与 interrupt 串行化。
                        checkpoint = self._next_checkpoint(
                            checkpoint,
                            phase=AgentRunPhase.FINAL_READY,
                            final_answer=action.answer,
                        )
                    async for event in self._commit_final(
                        state=state,
                        checkpoint=checkpoint,
                        runtime=runtime,
                        step=final_step,
                    ):
                        yield event
                    return

                # 工具动作必须先持久化，恢复时才不会重新调用模型生成同一动作。
                pending_action = action
                if runtime is not None:
                    checkpoint = self._next_checkpoint(
                        checkpoint,
                        phase=AgentRunPhase.BEFORE_TOOL,
                        pending_action=serialize_agent_action(
                            action,
                            tool_call_id=stable_tool_call_id(
                                run_id=state.run_id, step_index=index
                            ),
                        ),
                    )
                    commit = await runtime.commit_checkpoint(
                        checkpoint=checkpoint,
                        pause_when_interrupted=True,
                    )
                    checkpoint = commit.checkpoint
                    if commit.paused:
                        state.status = AgentRunStatus.INTERRUPTED
                        yield self._build_event(
                            type=AgentRunEventType.RUN_INTERRUPTED,
                            state=state,
                            checkpoint=checkpoint,
                            result=_build_result(state),
                            stop_reason=_stop_reason_value(commit.stop_reason),
                        )
                        return
                phase = AgentRunPhase.BEFORE_TOOL

            if phase is AgentRunPhase.BEFORE_TOOL:
                # 工具尚未开始：再次原子检查打断后再持久化 in-flight 标记。
                if runtime is not None:
                    checkpoint = self._next_checkpoint(
                        checkpoint,
                        phase=AgentRunPhase.TOOL_IN_FLIGHT,
                    )
                    commit = await runtime.commit_checkpoint(
                        checkpoint=checkpoint,
                        pause_when_interrupted=True,
                    )
                    checkpoint = commit.checkpoint
                    if commit.paused:
                        state.status = AgentRunStatus.INTERRUPTED
                        yield self._build_event(
                            type=AgentRunEventType.RUN_INTERRUPTED,
                            state=state,
                            checkpoint=checkpoint,
                            result=_build_result(state),
                            stop_reason=_stop_reason_value(commit.stop_reason),
                        )
                        return
                phase = AgentRunPhase.TOOL_IN_FLIGHT

            if phase is not AgentRunPhase.TOOL_IN_FLIGHT:  # pragma: no cover - 防御分支
                raise RuntimeError(f"unsupported execution phase: {phase}")
            if pending_action is None:  # pragma: no cover - 防御分支
                raise RuntimeError("tool_in_flight phase requires a pending action")

            tool_call_id = action_tool_call_id(
                checkpoint.pending_action if checkpoint is not None else {},
                run_id=state.run_id,
                step_index=index,
            )
            action_result, error = await self._execute_tool_call(
                pending_action, tool_call_id=tool_call_id
            )
            step_status = AgentStepStatus.FAILED if error else AgentStepStatus.COMPLETED
            step = AgentStepRecord(
                index=index,
                action=pending_action,
                status=step_status,
                action_result=action_result,
                error=error,
                elapsed_ms=_elapsed_ms(started_at),
            )
            state.steps.append(step)

            if runtime is None:
                yield self._step_event(state=state, step=step, checkpoint=None)
                pending_action = None
                phase = AgentRunPhase.BEFORE_ACTION
                index += 1
                started_at = monotonic()
                continue

            checkpoint = self._next_checkpoint(
                checkpoint,
                phase=AgentRunPhase.BEFORE_ACTION,
                next_step_index=index + 1,
                pending_action=None,
            )
            commit = await runtime.commit_step(
                step=step,
                checkpoint=checkpoint,
                pause_when_interrupted=True,
            )
            checkpoint = commit.checkpoint
            yield self._step_event(state=state, step=step, checkpoint=checkpoint)
            if commit.paused:
                state.status = AgentRunStatus.INTERRUPTED
                yield self._build_event(
                    type=AgentRunEventType.RUN_INTERRUPTED,
                    state=state,
                    checkpoint=checkpoint,
                    result=_build_result(state),
                    stop_reason=_stop_reason_value(commit.stop_reason),
                )
                return
            pending_action = None
            phase = AgentRunPhase.BEFORE_ACTION
            index += 1
            started_at = monotonic()

        # 达到最大步数时不伪造答案，调用方可以根据 status 决定重试或降级回复。
        state.status = AgentRunStatus.MAX_STEPS_REACHED
        if runtime is None:
            yield self._build_event(
                type=AgentRunEventType.RUN_COMPLETED,
                state=state,
                checkpoint=None,
                result=_build_result(state),
            )
            return

        checkpoint = self._next_checkpoint(
            checkpoint,
            phase=AgentRunPhase.BEFORE_ACTION,
            next_step_index=index,
            pending_action=None,
        )
        commit = await runtime.commit_terminal(
            checkpoint=checkpoint,
            result=_build_result(state),
            pause_when_interrupted=True,
        )
        checkpoint = commit.checkpoint
        if commit.paused:
            state.status = AgentRunStatus.INTERRUPTED
            yield self._build_event(
                type=AgentRunEventType.RUN_INTERRUPTED,
                state=state,
                checkpoint=checkpoint,
                result=_build_result(state),
                stop_reason=_stop_reason_value(commit.stop_reason),
            )
            return
        yield self._build_event(
            type=AgentRunEventType.RUN_COMPLETED,
            state=state,
            checkpoint=checkpoint,
            result=_build_result(state),
        )

    async def _commit_final(
        self,
        *,
        state: AgentRunState,
        checkpoint: AgentCheckpoint | None,
        runtime: AgentRunRuntime | None,
        step: AgentStepRecord,
    ) -> AsyncIterator[AgentRunEvent]:
        """提交已具备 final 的终态（新生成或恢复 final_ready 现场时使用）。"""
        if runtime is None:  # pragma: no cover - 受管理路径才会走到这里
            yield self._step_event(state=state, step=step, checkpoint=None)
            yield self._build_event(
                type=AgentRunEventType.RUN_COMPLETED,
                state=state,
                checkpoint=None,
                result=_build_result(state),
            )
            return

        commit = await runtime.commit_terminal(
            checkpoint=checkpoint,
            result=_build_result(state),
            pause_when_interrupted=True,
        )
        checkpoint = commit.checkpoint
        yield self._step_event(state=state, step=step, checkpoint=checkpoint)
        if commit.paused:
            state.status = AgentRunStatus.INTERRUPTED
            yield self._build_event(
                type=AgentRunEventType.RUN_INTERRUPTED,
                state=state,
                checkpoint=checkpoint,
                result=_build_result(state),
                stop_reason=_stop_reason_value(commit.stop_reason),
            )
            return
        yield self._build_event(
            type=AgentRunEventType.RUN_COMPLETED,
            state=state,
            checkpoint=checkpoint,
            result=_build_result(state),
        )

    def _require_checkpoint(
        self, checkpoint: AgentCheckpoint | None
    ) -> AgentCheckpoint:
        """受管理模式启动时要求 runtime 已提交初始 checkpoint。"""
        if checkpoint is None:
            raise CheckpointValidationError(
                "managed run requires a committed initial checkpoint"
            )
        return checkpoint

    async def _control_stop_reason(self, runtime: AgentRunRuntime | None) -> str | None:
        """在调用模型前读取控制状态；返回停止原因表示不应启动新动作。"""
        if runtime is None:
            return None
        control = await runtime.load_control_state()
        if not control.should_stop:
            return None
        if control.lease_owned and control.interrupt_requested:
            checkpoint = self._require_checkpoint(await runtime.load_checkpoint())
            await runtime.commit_checkpoint(
                checkpoint=checkpoint.next_revision(),
                pause_when_interrupted=True,
            )
        if control.stop_reason is not None:
            return control.stop_reason.value
        return "interrupt_requested"

    def _next_checkpoint(
        self,
        checkpoint: AgentCheckpoint | None,
        **changes: Any,
    ) -> AgentCheckpoint:
        """在受管理模式下递增 revision 生成下一个 checkpoint。"""
        if checkpoint is None:  # pragma: no cover - 受管理路径必定已有 checkpoint
            raise CheckpointValidationError("managed run lost its checkpoint")
        return checkpoint.next_revision(**changes)

    def _step_event(
        self,
        *,
        state: AgentRunState,
        step: AgentStepRecord,
        checkpoint: AgentCheckpoint | None,
    ) -> AgentRunEvent:
        return self._build_event(
            type=AgentRunEventType.STEP_COMPLETED,
            state=state,
            checkpoint=checkpoint,
            step=step,
        )

    def _build_event(
        self,
        *,
        type: AgentRunEventType,
        state: AgentRunState,
        checkpoint: AgentCheckpoint | None,
        step: AgentStepRecord | None = None,
        result: AgentRunResult | None = None,
        stop_reason: str | None = None,
    ) -> AgentRunEvent:
        return AgentRunEvent(
            type=type,
            run_id=state.run_id,
            status=state.status,
            step=step,
            result=result,
            checkpoint_revision=checkpoint.revision if checkpoint is not None else None,
            stop_reason=stop_reason,
        )

    async def _execute_tool_call(
        self, action: AgentToolCall, *, tool_call_id: str | None = None
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

        args = action.args
        if (
            tool_call_id is not None
            and tool.replay_policy is ToolReplayPolicy.IDEMPOTENT
        ):
            # `idempotent` 工具按稳定调用键去重或查询既有结果，避免恢复时制造重复副作用。
            args = {**args, AGENT_TOOL_CALL_ID_ARG: tool_call_id}

        try:
            result = tool.handler(args)
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


def _stop_reason_value(reason: Any) -> str | None:
    """把 runtime 返回的停止原因归一为字符串。"""
    if reason is None:
        return None
    value = getattr(reason, "value", reason)
    return value if isinstance(value, str) else None


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
