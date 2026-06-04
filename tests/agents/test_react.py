import pytest

from pkg.agents import (
    AgentDecisionContext,
    AgentFinal,
    AgentRunStatus,
    AgentStepStatus,
    AgentToolCall,
    InvalidAgentDecisionError,
    ReActAgent,
    StructuredTool,
    ToolExecutionError,
    UnknownToolError,
    parse_agent_decision,
)


class FunctionDecisionMaker:
    def __init__(self, decide_next):
        self._decide_next = decide_next

    async def decide_next(self, context: AgentDecisionContext):
        return await self._decide_next(context)


def build_search_tool() -> StructuredTool:
    return StructuredTool(
        name="search_order",
        description="Query order status by order id.",
        parameters_schema={
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
        handler=lambda args: {"order_id": args["order_id"], "status": "shipped"},
    )


@pytest.mark.asyncio
async def test_react_agent_runs_tool_then_final_answer() -> None:
    async def decide_next(context: AgentDecisionContext):
        assert context.user_input == "查询订单 123"
        assert context.tools[0].name == "search_order"
        if not context.state.steps:
            return AgentToolCall(tool="search_order", args={"order_id": "123"})

        observation = context.state.steps[-1].observation
        assert observation == {"order_id": "123", "status": "shipped"}
        return AgentFinal(answer="订单 123 已发货。")

    agent = ReActAgent(
        decision_maker=FunctionDecisionMaker(decide_next),
        tools=[build_search_tool()],
        max_steps=3,
    )

    result = await agent.run(user_input="查询订单 123", run_id="run_1")

    assert result.run_id == "run_1"
    assert result.status == AgentRunStatus.COMPLETED
    assert result.final_answer == "订单 123 已发货。"
    assert len(result.steps) == 2
    assert result.steps[0].status == AgentStepStatus.COMPLETED
    assert result.steps[0].observation == {"order_id": "123", "status": "shipped"}
    assert result.steps[1].decision == AgentFinal(answer="订单 123 已发货。")


@pytest.mark.asyncio
async def test_react_agent_records_unknown_tool_error_and_continues() -> None:
    async def decide_next(context: AgentDecisionContext):
        if not context.state.steps:
            return AgentToolCall(tool="missing_tool", args={})

        assert context.state.steps[-1].error == "unknown tool: missing_tool"
        return AgentFinal(answer="没有可用工具处理该请求。")

    agent = ReActAgent(
        decision_maker=FunctionDecisionMaker(decide_next),
        tools=[build_search_tool()],
        max_steps=2,
    )

    result = await agent.run(user_input="test")

    assert result.status == AgentRunStatus.COMPLETED
    assert result.final_answer == "没有可用工具处理该请求。"
    assert result.steps[0].status == AgentStepStatus.FAILED
    assert result.steps[0].observation == {"error": "unknown tool: missing_tool"}


@pytest.mark.asyncio
async def test_react_agent_records_tool_exception() -> None:
    def broken_tool(_args):
        raise RuntimeError("backend unavailable")

    async def decide_next(context: AgentDecisionContext):
        if not context.state.steps:
            return AgentToolCall(tool="broken_tool", args={})

        assert context.state.steps[-1].error == "RuntimeError: backend unavailable"
        return AgentFinal(answer="工具暂不可用。")

    agent = ReActAgent(
        decision_maker=FunctionDecisionMaker(decide_next),
        tools=[
            StructuredTool(
                name="broken_tool",
                description="Always fails.",
                parameters_schema={"type": "object"},
                handler=broken_tool,
            )
        ],
        max_steps=2,
    )

    result = await agent.run(user_input="test")

    assert result.status == AgentRunStatus.COMPLETED
    assert result.steps[0].status == AgentStepStatus.FAILED
    assert result.steps[0].observation == {"error": "RuntimeError: backend unavailable"}


@pytest.mark.asyncio
async def test_react_agent_stops_when_max_steps_reached() -> None:
    async def decide_next(_context: AgentDecisionContext):
        return AgentToolCall(tool="search_order", args={"order_id": "123"})

    agent = ReActAgent(
        decision_maker=FunctionDecisionMaker(decide_next),
        tools=[build_search_tool()],
        max_steps=2,
    )

    result = await agent.run(user_input="loop")

    assert result.status == AgentRunStatus.MAX_STEPS_REACHED
    assert result.final_answer is None
    assert len(result.steps) == 2


@pytest.mark.asyncio
async def test_react_agent_raises_when_error_capture_disabled() -> None:
    async def decide_next(_context: AgentDecisionContext):
        return AgentToolCall(tool="missing_tool", args={})

    agent = ReActAgent(
        decision_maker=FunctionDecisionMaker(decide_next),
        tools=[build_search_tool()],
        max_steps=1,
        capture_tool_errors=False,
    )

    with pytest.raises(UnknownToolError, match="unknown tool: missing_tool"):
        await agent.run(user_input="test")


@pytest.mark.asyncio
async def test_react_agent_rejects_non_mapping_tool_args() -> None:
    async def decide_next(_context: AgentDecisionContext):
        return AgentToolCall(tool="search_order", args=["bad"])

    agent = ReActAgent(
        decision_maker=FunctionDecisionMaker(decide_next),
        tools=[build_search_tool()],
        max_steps=1,
    )

    result = await agent.run(user_input="test")

    assert result.status == AgentRunStatus.MAX_STEPS_REACHED
    assert result.steps[0].status == AgentStepStatus.FAILED
    assert result.steps[0].error == "tool args must be a mapping"


@pytest.mark.asyncio
async def test_react_agent_rejects_invalid_decision_maker_decision() -> None:
    async def decide_next(_context: AgentDecisionContext):
        return None

    agent = ReActAgent(
        decision_maker=FunctionDecisionMaker(decide_next),
        tools=[build_search_tool()],
        max_steps=1,
    )

    with pytest.raises(InvalidAgentDecisionError, match="invalid agent decision: None"):
        await agent.run(user_input="test")


def test_react_agent_rejects_duplicate_tool_names() -> None:
    tool = build_search_tool()

    with pytest.raises(ValueError, match="duplicate tool names: search_order"):
        ReActAgent(
            decision_maker=FunctionDecisionMaker(
                lambda _context: AgentFinal(answer="done")
            ),
            tools=[tool, tool],
        )


def test_react_agent_rejects_invalid_max_steps() -> None:
    with pytest.raises(ValueError, match="max_steps must be greater than 0"):
        ReActAgent(
            decision_maker=FunctionDecisionMaker(
                lambda _context: AgentFinal(answer="done")
            ),
            tools=[],
            max_steps=0,
        )


def test_structured_tool_requires_name_and_description() -> None:
    with pytest.raises(ValueError, match="tool name cannot be empty"):
        StructuredTool(
            name="", description="desc", parameters_schema={}, handler=lambda args: None
        )

    with pytest.raises(ValueError, match="tool description cannot be empty"):
        StructuredTool(
            name="tool", description="", parameters_schema={}, handler=lambda args: None
        )


def test_parse_agent_decision_parses_final_payload() -> None:
    decision = parse_agent_decision({"type": "final", "answer": "done"})

    assert decision == AgentFinal(answer="done")


def test_parse_agent_decision_parses_tool_call_payload() -> None:
    decision = parse_agent_decision(
        {
            "type": "tool_call",
            "tool": "search_order",
            "args": {"order_id": "123"},
            "call_id": "call_1",
        }
    )

    assert decision == AgentToolCall(
        tool="search_order", args={"order_id": "123"}, call_id="call_1"
    )


def test_parse_agent_decision_rejects_invalid_payload() -> None:
    with pytest.raises(
        InvalidAgentDecisionError,
        match="unsupported decision type: 'unknown'",
    ):
        parse_agent_decision({"type": "unknown"})

    with pytest.raises(
        InvalidAgentDecisionError,
        match="tool_call decision requires mapping args",
    ):
        parse_agent_decision(
            {"type": "tool_call", "tool": "search_order", "args": ["bad"]}
        )


@pytest.mark.asyncio
async def test_react_agent_wraps_tool_exception_when_error_capture_disabled() -> None:
    def broken_tool(_args):
        raise ValueError("bad input")

    async def decide_next(_context: AgentDecisionContext):
        return AgentToolCall(tool="broken_tool", args={})

    agent = ReActAgent(
        decision_maker=FunctionDecisionMaker(decide_next),
        tools=[
            StructuredTool(
                name="broken_tool",
                description="Always fails.",
                parameters_schema={"type": "object"},
                handler=broken_tool,
            )
        ],
        capture_tool_errors=False,
    )

    with pytest.raises(ToolExecutionError, match="ValueError: bad input"):
        await agent.run(user_input="test")
