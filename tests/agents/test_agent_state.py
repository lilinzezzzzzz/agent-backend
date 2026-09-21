"""checkpoint codec 与运行阶段 contract 的单元测试。"""

import pytest

from pkg.agents import (
    CHECKPOINT_SCHEMA_VERSION,
    AgentCheckpoint,
    AgentCheckpointStep,
    AgentRunPhase,
    CheckpointSerializationError,
    CheckpointValidationError,
    UnsupportedCheckpointVersionError,
    decode_checkpoint,
    encode_checkpoint,
    stable_tool_call_id,
)

RUN_ID = "run_state_1"


def _tool_step(index: int, *, tool: str = "get_order_status") -> AgentCheckpointStep:
    return AgentCheckpointStep(
        index=index,
        status="completed",
        action={
            "type": "tool_call",
            "tool": tool,
            "args": {"order_id": "1001"},
            "call_id": None,
            "tool_call_id": stable_tool_call_id(run_id=RUN_ID, step_index=index),
        },
        action_result={"ok": True},
        elapsed_ms=1.5,
    )


def _checkpoint(**overrides) -> AgentCheckpoint:
    base = {
        "run_id": RUN_ID,
        "phase": AgentRunPhase.BEFORE_ACTION,
        "max_steps": 4,
        "user_input": "订单 1001 到哪了？",
        "definition_version": "v1",
        "session_context": {"session_id": "session_1"},
        "model_config": {"provider": "mimo", "model": "mimo-v2"},
        "route": "order",
        "agent_name": "order_support",
    }
    base.update(overrides)
    return AgentCheckpoint(**base)


def test_encode_decode_roundtrip_preserves_snapshot() -> None:
    checkpoint = _checkpoint(
        revision=3,
        next_step_index=1,
        steps=(_tool_step(0),),
    )

    payload = encode_checkpoint(checkpoint)
    restored = decode_checkpoint(payload)

    assert restored == checkpoint
    assert restored.schema_version == CHECKPOINT_SCHEMA_VERSION
    assert restored.steps[0].action["tool_call_id"] == stable_tool_call_id(
        run_id=RUN_ID, step_index=0
    )


def test_decode_accepts_json_text_and_rejects_invalid_json() -> None:
    assert decode_checkpoint(encode_checkpoint(_checkpoint())) == _checkpoint()

    with pytest.raises(CheckpointValidationError):
        decode_checkpoint("{not json")


def test_unknown_schema_version_is_rejected() -> None:
    payload = encode_checkpoint(_checkpoint())
    payload["schema_version"] = CHECKPOINT_SCHEMA_VERSION + 1

    with pytest.raises(UnsupportedCheckpointVersionError):
        decode_checkpoint(payload)


def test_non_contiguous_steps_are_rejected() -> None:
    checkpoint = _checkpoint(next_step_index=2, steps=(_tool_step(0), _tool_step(2)))

    with pytest.raises(CheckpointValidationError):
        encode_checkpoint(checkpoint)


def test_next_step_index_must_match_committed_steps() -> None:
    checkpoint = _checkpoint(next_step_index=2, steps=(_tool_step(0),))

    with pytest.raises(CheckpointValidationError):
        encode_checkpoint(checkpoint)


def test_phase_and_payload_must_be_consistent() -> None:
    with pytest.raises(CheckpointValidationError):
        encode_checkpoint(_checkpoint(phase=AgentRunPhase.FINAL_READY))

    with pytest.raises(CheckpointValidationError):
        encode_checkpoint(
            _checkpoint(phase=AgentRunPhase.BEFORE_ACTION, final_answer="不该出现")
        )

    with pytest.raises(CheckpointValidationError):
        encode_checkpoint(_checkpoint(phase=AgentRunPhase.TOOL_IN_FLIGHT))


def test_tool_in_flight_requires_a_tool_call_pending_action() -> None:
    checkpoint = _checkpoint(
        phase=AgentRunPhase.TOOL_IN_FLIGHT,
        pending_action={
            "type": "tool_call",
            "tool": "get_order_status",
            "args": {"order_id": "1001"},
            "call_id": None,
            "tool_call_id": stable_tool_call_id(run_id=RUN_ID, step_index=0),
        },
    )

    assert decode_checkpoint(encode_checkpoint(checkpoint)) == checkpoint

    with pytest.raises(CheckpointValidationError):
        encode_checkpoint(
            _checkpoint(
                phase=AgentRunPhase.TOOL_IN_FLIGHT,
                pending_action={"type": "tool_call", "args": {}},
            )
        )


def test_missing_tool_name_in_committed_step_is_rejected() -> None:
    broken = AgentCheckpointStep(
        index=0,
        status="completed",
        action={"type": "tool_call", "args": {}},
    )

    with pytest.raises(CheckpointValidationError):
        encode_checkpoint(_checkpoint(next_step_index=1, steps=(broken,)))


def test_non_serializable_state_is_rejected() -> None:
    checkpoint = _checkpoint(
        next_step_index=1,
        steps=(
            AgentCheckpointStep(
                index=0,
                status="completed",
                action={
                    "type": "tool_call",
                    "tool": "get_order_status",
                    "args": {"order_id": "1001"},
                    "call_id": None,
                },
                action_result={"handle": object()},
            ),
        ),
    )

    with pytest.raises(CheckpointSerializationError):
        encode_checkpoint(checkpoint)


def test_checkpoint_size_limit_is_enforced() -> None:
    checkpoint = _checkpoint(session_context={"blob": "x" * 512})

    with pytest.raises(CheckpointSerializationError):
        encode_checkpoint(checkpoint, max_bytes=64)

    with pytest.raises(CheckpointSerializationError):
        decode_checkpoint("{'a': 1}", max_bytes=1)


def test_next_revision_increments_and_ignores_caller_revision() -> None:
    checkpoint = _checkpoint(revision=5)

    bumped = checkpoint.next_revision(phase=AgentRunPhase.BEFORE_ACTION, revision=99)

    assert bumped.revision == 6
    assert bumped.phase is AgentRunPhase.BEFORE_ACTION


def test_stable_tool_call_id_depends_on_run_and_step_only() -> None:
    assert stable_tool_call_id(run_id=RUN_ID, step_index=0) == f"{RUN_ID}:0"
    assert stable_tool_call_id(run_id=RUN_ID, step_index=1) == f"{RUN_ID}:1"

    with pytest.raises(CheckpointValidationError):
        stable_tool_call_id(run_id="", step_index=0)
    with pytest.raises(CheckpointValidationError):
        stable_tool_call_id(run_id=RUN_ID, step_index=-1)
