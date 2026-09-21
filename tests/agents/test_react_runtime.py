"""受管理 ReAct 执行的恢复语义测试（内存 runtime，不依赖数据库）。"""

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import pytest

from pkg.agents import (
    AgentCheckpoint,
    AgentCheckpointCommit,
    AgentControlState,
    AgentControlStatus,
    AgentControlStopReason,
    AgentFinal,
    AgentRunEventType,
    AgentRunPhase,
    AgentRunStatus,
    AgentStepRecord,
    AgentStepStatus,
    AgentToolCall,
    ReActAgent,
    StructuredTool,
    ToolReplayPolicy,
    UnsafeAgentResumeError,
    stable_tool_call_id,
    step_record_to_checkpoint_step,
)

RUN_ID = "run_react_1"
DEFINITION_VERSION = "test-v1"


class RecordingActionMaker:
    """记录每次被调用时看到的步骤数，用于证明已提交步骤不会重做。"""

    def __init__(self, actions: list[AgentToolCall | AgentFinal]):
        self._actions = list(actions)
        self.calls: list[int] = []

    async def make_next_action(self, context):
        self.calls.append(len(context.state.steps))
        if not self._actions:
            return AgentFinal(answer="没有更多动作")
        return self._actions.pop(0)


class InMemoryAgentRuntime:
    """实现 `AgentRunRuntime` 的最小内存语义，用于安全点与恢复测试。"""

    def __init__(
        self,
        *,
        checkpoint: AgentCheckpoint,
        pause_before_phase: AgentRunPhase | None = None,
        lease_owned: bool = True,
        interrupt_requested: bool = False,
    ):
        self.checkpoint = checkpoint
        self.pause_before_phase = pause_before_phase
        self.lease_owned = lease_owned
        self.status = (
            AgentControlStatus.INTERRUPT_REQUESTED
            if interrupt_requested
            else AgentControlStatus.RUNNING
        )
        self.committed_phases: list[AgentRunPhase] = []
        self.terminal_status: str | None = None
        self.terminal_result = None

    async def load_checkpoint(self) -> AgentCheckpoint:
        return self.checkpoint

    async def load_control_state(self) -> AgentControlState:
        return AgentControlState(
            status=self.status,
            revision=self.checkpoint.revision,
            attempt_no=1,
            interrupt_requested=self.status is AgentControlStatus.INTERRUPT_REQUESTED,
            lease_owned=self.lease_owned,
            stop_reason=(
                None if self.lease_owned else AgentControlStopReason.LEASE_LOST
            ),
        )

    async def commit_checkpoint(
        self, *, checkpoint: AgentCheckpoint, pause_when_interrupted: bool = False
    ) -> AgentCheckpointCommit:
        paused = (
            pause_when_interrupted
            and self.status is AgentControlStatus.INTERRUPT_REQUESTED
        )
        if paused:
            # 打断先提交：保留上一个安全现场，而不是推进到新阶段。
            persisted = self.checkpoint.next_revision()
            self.status = AgentControlStatus.INTERRUPTED
        else:
            persisted = checkpoint
        self.checkpoint = persisted
        self.committed_phases.append(persisted.phase)
        self._arm_interrupt_if_requested(persisted)
        return AgentCheckpointCommit(
            checkpoint=persisted,
            paused=paused,
            stop_reason=(
                AgentControlStopReason.INTERRUPT_REQUESTED if paused else None
            ),
        )

    async def commit_step(
        self,
        *,
        step: AgentStepRecord,
        checkpoint: AgentCheckpoint,
        pause_when_interrupted: bool = False,
    ) -> AgentCheckpointCommit:
        steps = checkpoint.steps + (
            step_record_to_checkpoint_step(
                step,
                tool_call_id=stable_tool_call_id(run_id=RUN_ID, step_index=step.index),
            ),
        )
        persisted = replace(checkpoint, steps=steps, next_step_index=len(steps))
        paused = (
            pause_when_interrupted
            and self.status is AgentControlStatus.INTERRUPT_REQUESTED
        )
        if paused:
            self.status = AgentControlStatus.INTERRUPTED
        self.checkpoint = persisted
        self._arm_interrupt_if_requested(persisted)
        return AgentCheckpointCommit(
            checkpoint=persisted,
            paused=paused,
            stop_reason=(
                AgentControlStopReason.INTERRUPT_REQUESTED if paused else None
            ),
        )

    async def commit_terminal(
        self,
        *,
        checkpoint: AgentCheckpoint,
        result,
        pause_when_interrupted: bool = False,
    ) -> AgentCheckpointCommit:
        paused = (
            pause_when_interrupted
            and self.status is AgentControlStatus.INTERRUPT_REQUESTED
        )
        # 终态提交与 interrupt 串行化：暂停时只保留 final_ready 现场。
        persisted = checkpoint if paused else result_checkpoint(checkpoint, result)
        if paused:
            self.status = AgentControlStatus.INTERRUPTED
        else:
            self.terminal_status = result.status.value
            self.terminal_result = result
        self.checkpoint = persisted
        self._arm_interrupt_if_requested(persisted)
        return AgentCheckpointCommit(
            checkpoint=persisted,
            paused=paused,
            stop_reason=(
                AgentControlStopReason.INTERRUPT_REQUESTED if paused else None
            ),
        )

    def _arm_interrupt_if_requested(self, persisted: AgentCheckpoint) -> None:
        """到达指定阶段后模拟一次打断请求（下一次原子提交才会暂停）。"""
        if (
            self.pause_before_phase is not None
            and persisted.phase is self.pause_before_phase
            and self.status is AgentControlStatus.RUNNING
        ):
            self.status = AgentControlStatus.INTERRUPT_REQUESTED


def result_checkpoint(checkpoint: AgentCheckpoint, result) -> AgentCheckpoint:
    """按终态结果补齐 final step，模拟存储层的原子终态提交。"""
    steps = checkpoint.steps
    for step in result.steps:
        if step.index < len(steps):
            continue
        steps = steps + (
            step_record_to_checkpoint_step(
                step,
                tool_call_id=stable_tool_call_id(run_id=RUN_ID, step_index=step.index),
            ),
        )
    if len(steps) == len(checkpoint.steps):
        return checkpoint
    return replace(checkpoint, steps=steps, next_step_index=len(steps))


def build_tool(
    name: str,
    *,
    policy: ToolReplayPolicy = ToolReplayPolicy.REPLAY_SAFE,
    calls: list[Mapping[str, Any]] | None = None,
) -> StructuredTool:
    def handler(args):
        if calls is not None:
            calls.append(dict(args))
        return {"ok": True, "tool": name, "args": dict(args)}

    return StructuredTool(
        name=name,
        description=f"{name} tool",
        parameters_schema={"type": "object", "properties": {}},
        handler=handler,
        replay_policy=policy,
    )


def build_agent(
    action_maker: RecordingActionMaker,
    tools: list[StructuredTool],
    *,
    max_steps: int = 4,
) -> ReActAgent:
    return ReActAgent(
        action_maker=action_maker,
        tools=tools,
        max_steps=max_steps,
        capture_tool_errors=False,
        definition_version=DEFINITION_VERSION,
    )


def build_checkpoint(**overrides) -> AgentCheckpoint:
    base: dict[str, Any] = {
        "run_id": RUN_ID,
        "phase": AgentRunPhase.BEFORE_ACTION,
        "max_steps": 4,
        "user_input": "订单 1001 到哪了？",
        "definition_version": DEFINITION_VERSION,
        "session_context": {"session_id": "session_1"},
    }
    base.update(overrides)
    return AgentCheckpoint(**base)


@pytest.mark.asyncio
async def test_resume_does_not_redo_committed_steps() -> None:
    """已提交的步骤 0 不会被重做，恢复直接从步骤 1 继续。"""
    tool_calls: list[Mapping[str, Any]] = []
    action_maker = RecordingActionMaker(
        [
            AgentToolCall(tool="search_order", args={"order_id": "1001"}),
            AgentFinal(answer="订单 1001 已发货。"),
        ]
    )
    agent = build_agent(action_maker, [build_tool("search_order", calls=tool_calls)])

    # 第一次执行在步骤 0 提交后被 interrupt 暂停。
    runtime = InMemoryAgentRuntime(
        checkpoint=build_checkpoint(), pause_before_phase=AgentRunPhase.BEFORE_ACTION
    )
    first = await agent.run(
        user_input="订单 1001 到哪了？", run_id=RUN_ID, runtime=runtime
    )
    assert first.status is AgentRunStatus.INTERRUPTED
    assert runtime.checkpoint.next_step_index == 1
    assert len(tool_calls) == 1
    assert action_maker.calls == [0]

    # 恢复：步骤 0 不重做，动作序列只剩 final。
    runtime.status = AgentControlStatus.RUNNING
    resumed_actions = RecordingActionMaker([AgentFinal(answer="订单 1001 已发货。")])
    resumed_agent = build_agent(
        resumed_actions, [build_tool("search_order", calls=tool_calls)]
    )
    events = [
        event
        async for event in resumed_agent.resume_events(
            checkpoint=runtime.checkpoint, runtime=runtime
        )
    ]

    assert events[0].type is AgentRunEventType.RUN_RESUMED
    assert resumed_actions.calls == [1]
    assert len(tool_calls) == 1
    assert runtime.terminal_status == AgentRunStatus.COMPLETED.value
    assert runtime.terminal_result is not None
    assert runtime.terminal_result.steps[0].index == 0
    assert runtime.terminal_result.steps[1].index == 1


@pytest.mark.asyncio
async def test_resume_reuses_persisted_pending_action_without_model_call() -> None:
    """`before_tool` 现场直接执行已保存动作，不重新调用模型。"""
    tool_calls: list[Mapping[str, Any]] = []
    action_maker = RecordingActionMaker(
        [AgentToolCall(tool="search_order", args={"order_id": "1001"})]
    )
    tools = [build_tool("search_order", calls=tool_calls)]
    agent = build_agent(action_maker, tools)

    runtime = InMemoryAgentRuntime(
        checkpoint=build_checkpoint(),
        pause_before_phase=AgentRunPhase.BEFORE_TOOL,
    )
    first = await agent.run(
        user_input="订单 1001 到哪了？", run_id=RUN_ID, runtime=runtime
    )

    assert first.status is AgentRunStatus.INTERRUPTED
    assert tool_calls == []
    assert runtime.checkpoint.phase is AgentRunPhase.BEFORE_TOOL
    assert runtime.checkpoint.pending_action is not None
    assert runtime.checkpoint.pending_action["tool_call_id"] == f"{RUN_ID}:0"

    runtime.status = AgentControlStatus.RUNNING
    resumed_actions = RecordingActionMaker([AgentFinal(answer="已为你查询。")])
    resumed_agent = build_agent(
        resumed_actions, [build_tool("search_order", calls=tool_calls)]
    )
    result = await resumed_agent.resume(checkpoint=runtime.checkpoint, runtime=runtime)

    # 恢复时不会为步骤 0 重新调用模型：第一次 action maker 调用发生在步骤 0 提交之后。
    assert resumed_actions.calls == [1]
    assert len(tool_calls) == 1
    assert tool_calls[0] == {"order_id": "1001"}
    assert result.status is AgentRunStatus.COMPLETED
    assert result.steps[0].action.tool == "search_order"


@pytest.mark.asyncio
async def test_resume_does_not_reset_max_steps() -> None:
    """恢复沿用 checkpoint 的 max_steps，不能用反复 resume 绕过上限。"""
    action_maker = RecordingActionMaker(
        [
            AgentToolCall(tool="search_order", args={"order_id": "1"}),
            AgentToolCall(tool="search_order", args={"order_id": "2"}),
        ]
    )
    agent = build_agent(action_maker, [build_tool("search_order")], max_steps=2)
    checkpoint = build_checkpoint(
        max_steps=2,
        next_step_index=1,
        steps=(
            step_record_to_checkpoint_step(
                AgentStepRecord(
                    index=0,
                    action=AgentToolCall(tool="search_order", args={"order_id": "1"}),
                    status=AgentStepStatus.COMPLETED,
                ),
                tool_call_id=f"{RUN_ID}:0",
            ),
        ),
    )
    runtime = InMemoryAgentRuntime(checkpoint=checkpoint)

    result = await agent.resume(checkpoint=checkpoint, runtime=runtime)

    assert result.status is AgentRunStatus.MAX_STEPS_REACHED
    assert action_maker.calls == [1]
    assert runtime.terminal_status == AgentRunStatus.MAX_STEPS_REACHED.value
    assert runtime.checkpoint.max_steps == 2


@pytest.mark.asyncio
async def test_resume_from_final_ready_commits_without_model_or_tool_call() -> None:
    """`final_ready` 恢复不调用模型，直接完成终态提交。"""
    action_maker = RecordingActionMaker([AgentFinal(answer="不该被调用")])
    tool_calls: list[Mapping[str, Any]] = []
    agent = build_agent(action_maker, [build_tool("search_order", calls=tool_calls)])
    checkpoint = build_checkpoint(
        phase=AgentRunPhase.FINAL_READY,
        final_answer="订单 1001 已发货。",
    )
    runtime = InMemoryAgentRuntime(checkpoint=checkpoint)

    result = await agent.resume(checkpoint=checkpoint, runtime=runtime)

    assert result.status is AgentRunStatus.COMPLETED
    assert result.final_answer == "订单 1001 已发货。"
    assert action_maker.calls == []
    assert tool_calls == []
    assert runtime.terminal_status == AgentRunStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_non_replayable_tool_in_flight_refuses_resume() -> None:
    """飞行中的 non_replayable 工具拒绝自动恢复。"""
    action_maker = RecordingActionMaker([])
    agent = ReActAgent(
        action_maker=action_maker,
        tools=[build_tool("prepare_invoice", policy=ToolReplayPolicy.NON_REPLAYABLE)],
        max_steps=2,
        definition_version=DEFINITION_VERSION,
    )
    checkpoint = build_checkpoint(
        max_steps=2,
        phase=AgentRunPhase.TOOL_IN_FLIGHT,
        pending_action={
            "type": "tool_call",
            "tool": "prepare_invoice",
            "args": {},
            "call_id": None,
            "tool_call_id": f"{RUN_ID}:0",
        },
    )

    assessment = agent.assess_resume(checkpoint)
    assert assessment.decision.value == "recovery_required"
    assert assessment.tool == "prepare_invoice"

    runtime = InMemoryAgentRuntime(checkpoint=checkpoint)
    with pytest.raises(UnsafeAgentResumeError):
        async for _event in agent.resume_events(checkpoint=checkpoint, runtime=runtime):
            pass


@pytest.mark.asyncio
async def test_idempotent_tool_replay_receives_stable_call_id() -> None:
    """`idempotent` 工具重放时收到稳定的调用键，供下游去重。"""
    tool_calls: list[Mapping[str, Any]] = []
    agent = build_agent(
        RecordingActionMaker([]),
        [
            build_tool(
                "submit_refund",
                policy=ToolReplayPolicy.IDEMPOTENT,
                calls=tool_calls,
            )
        ],
        max_steps=2,
    )
    checkpoint = build_checkpoint(
        max_steps=2,
        phase=AgentRunPhase.TOOL_IN_FLIGHT,
        pending_action={
            "type": "tool_call",
            "tool": "submit_refund",
            "args": {"order_id": "1001"},
            "call_id": None,
            "tool_call_id": f"{RUN_ID}:0",
        },
    )
    runtime = InMemoryAgentRuntime(checkpoint=checkpoint)
    runtime_action_maker = RecordingActionMaker([AgentFinal(answer="退款已提交。")])
    agent = build_agent(
        runtime_action_maker,
        [
            build_tool(
                "submit_refund",
                policy=ToolReplayPolicy.IDEMPOTENT,
                calls=tool_calls,
            )
        ],
        max_steps=2,
    )

    result = await agent.resume(checkpoint=checkpoint, runtime=runtime)

    assert result.status is AgentRunStatus.COMPLETED
    assert tool_calls[0]["_agent_tool_call_id"] == f"{RUN_ID}:0"


@pytest.mark.asyncio
async def test_unknown_tool_in_checkpoint_rejects_resume() -> None:
    """checkpoint 引用了缺失工具时拒绝恢复，而不是丢弃状态重跑。"""
    agent = build_agent(RecordingActionMaker([]), [build_tool("search_order")])
    checkpoint = build_checkpoint(
        next_step_index=1,
        steps=(
            step_record_to_checkpoint_step(
                AgentStepRecord(
                    index=0,
                    action=AgentToolCall(tool="removed_tool", args={}),
                    status=AgentStepStatus.COMPLETED,
                ),
                tool_call_id=f"{RUN_ID}:0",
            ),
        ),
    )

    with pytest.raises(Exception) as excinfo:
        agent.assess_resume(checkpoint)
    assert "unknown tool" in str(excinfo.value)


@pytest.mark.asyncio
async def test_lease_loss_stops_before_generating_next_action() -> None:
    """失去 lease 时不再启动新的模型调用，并以 lease_lost 暂停。"""
    action_maker = RecordingActionMaker(
        [AgentToolCall(tool="search_order", args={"order_id": "1001"})]
    )
    agent = build_agent(action_maker, [build_tool("search_order")])
    runtime = InMemoryAgentRuntime(checkpoint=build_checkpoint(), lease_owned=False)

    events = [
        event
        async for event in agent.run_events(
            user_input="订单 1001 到哪了？", run_id=RUN_ID, runtime=runtime
        )
    ]

    assert events[-1].type is AgentRunEventType.RUN_INTERRUPTED
    assert events[-1].stop_reason == AgentControlStopReason.LEASE_LOST.value
    assert action_maker.calls == []


@pytest.mark.asyncio
async def test_definition_version_mismatch_rejects_resume() -> None:
    """definition_version 不兼容时拒绝恢复，不静默切换新 prompt/工具。"""
    agent = build_agent(RecordingActionMaker([]), [build_tool("search_order")])
    checkpoint = build_checkpoint(definition_version="other-v2")

    with pytest.raises(Exception) as excinfo:
        agent.assess_resume(checkpoint)
    assert "definition_version" in str(excinfo.value)
