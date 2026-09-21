"""Agent 运行现场（checkpoint）的状态 contract 与版本化 JSON codec。

该模块只描述可持久化的运行现场，不持有数据库、网络或业务对象：

- `AgentRunPhase` 表示执行器当前停在哪个安全点；
- `ToolReplayPolicy` 表示工具在崩溃/打断后能否安全重放；
- `AgentCheckpoint` 是恢复时唯一的执行状态来源；
- `encode_checkpoint()` / `decode_checkpoint()` 负责版本化 JSON 编解码，
  无法序列化的状态、未知版本、步骤不连续和阶段自相矛盾都会被显式拒绝。

调用方（应用层或测试）负责把 checkpoint 与业务存储绑定；本模块不做 I/O。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from pkg.toolkit.json import orjson_dumps_bytes, orjson_loads

CHECKPOINT_SCHEMA_VERSION = 1
"""当前 checkpoint JSON 格式版本；不兼容变更必须递增该值。"""

SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS = frozenset({CHECKPOINT_SCHEMA_VERSION})
"""允许恢复的 checkpoint 格式版本；未知版本必须拒绝恢复。"""

DEFAULT_CHECKPOINT_MAX_BYTES = 1024 * 1024
"""checkpoint JSON 默认大小上限（1 MiB）。"""

_ACTION_TYPES = frozenset({"tool_call", "final"})


class AgentRunPhase(StrEnum):
    """checkpoint 记录的执行阶段。

    阶段描述“已经持久化到哪一步”，而不是内存中的临时进度，因此恢复时可以据此
    判断哪些动作必须复用、哪些调用允许重做。
    """

    ROUTING = "routing"
    BEFORE_ACTION = "before_action"
    BEFORE_TOOL = "before_tool"
    TOOL_IN_FLIGHT = "tool_in_flight"
    FINAL_READY = "final_ready"


class ToolReplayPolicy(StrEnum):
    """工具在恢复时允许的重放策略。

    未显式声明的工具按 `NON_REPLAYABLE` 处理；不得因为函数名包含 query/get 就推断安全。
    """

    REPLAY_SAFE = "replay_safe"
    IDEMPOTENT = "idempotent"
    NON_REPLAYABLE = "non_replayable"


class CheckpointError(ValueError):
    """checkpoint 编解码或校验失败基类。"""


class UnsupportedCheckpointVersionError(CheckpointError):
    """checkpoint schema_version 不在支持列表内。"""


class CheckpointSerializationError(CheckpointError):
    """checkpoint 含不可序列化状态或超过大小上限。"""


class CheckpointValidationError(CheckpointError):
    """checkpoint 结构、步骤连续性或阶段一致性校验失败。"""


@dataclass(frozen=True, slots=True)
class AgentCheckpointStep:
    """已提交步骤在 checkpoint 中的快照。

    `action` 是序列化后的动作字典（`tool_call` 或 `final`），与 `agent_run_step`
    查询表使用同一份语义；执行以本快照为准，步骤表不得独立修订。
    """

    index: int
    status: str
    action: Mapping[str, Any]
    action_result: Any = None
    error: str | None = None
    elapsed_ms: float = 0.0

    @property
    def action_type(self) -> str:
        """返回动作类型字符串，未知类型返回空串。"""
        action_type = self.action.get("type")
        return action_type if isinstance(action_type, str) else ""

    @property
    def tool(self) -> str | None:
        """返回工具名；非工具调用动作返回 None。"""
        tool = self.action.get("tool")
        return tool if isinstance(tool, str) else None

    def to_payload(self) -> dict[str, Any]:
        """转换为 JSON 兼容载荷。"""
        return {
            "index": self.index,
            "status": self.status,
            "action": dict(self.action),
            "action_result": self.action_result,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass(frozen=True, slots=True)
class AgentCheckpoint:
    """一次受管理运行的可持久化执行现场。

    Attributes:
        run_id: 与持久化 run 绑定，恢复时保持不变。
        phase: 当前安全点。
        next_step_index: 下一个未提交步骤序号；恒等于 `len(steps)`。
        max_steps: 整个 run 的总步数上限；恢复不重置。
        user_input: 创建时冻结的用户问题。
        session_context: 创建时冻结的有界会话上下文。
        route: 服务端选择的业务域；路由成功后冻结。
        agent_name: 与 route 对应的业务 Agent 名称。
        definition_version: Builder / prompt / 工具语义的兼容版本。
        model_config: 模型标识与影响行为的非敏感参数；不含凭据。
        steps: 已完成步骤的有界快照。
        pending_action: 已持久化、尚未完成的结构化 action。
        final_answer: final 已生成但尚未提交的回答。
        revision: 每次持久化递增的乐观并发版本。
        schema_version: JSON 格式版本。
    """

    run_id: str
    phase: AgentRunPhase
    max_steps: int
    user_input: str
    definition_version: str
    next_step_index: int = 0
    session_context: Mapping[str, Any] = field(default_factory=dict)
    route: str | None = None
    agent_name: str | None = None
    model_config: Mapping[str, Any] = field(default_factory=dict)
    steps: tuple[AgentCheckpointStep, ...] = ()
    pending_action: Mapping[str, Any] | None = None
    final_answer: str | None = None
    revision: int = 0
    schema_version: int = CHECKPOINT_SCHEMA_VERSION

    def next_revision(self, **changes: Any) -> AgentCheckpoint:
        """返回 revision 递增后的新 checkpoint。

        传入 `revision` 会被忽略，避免调用方绕过乐观并发版本递增。
        """
        changes.pop("revision", None)
        return replace(self, revision=self.revision + 1, **changes)

    def to_payload(self) -> dict[str, Any]:
        """转换为 JSON 兼容载荷（不做序列化校验）。"""
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "run_id": self.run_id,
            "phase": self.phase.value,
            "next_step_index": self.next_step_index,
            "max_steps": self.max_steps,
            "user_input": self.user_input,
            "session_context": dict(self.session_context),
            "route": self.route,
            "agent_name": self.agent_name,
            "definition_version": self.definition_version,
            "model_config": dict(self.model_config),
            "steps": [step.to_payload() for step in self.steps],
            "pending_action": (
                dict(self.pending_action) if self.pending_action is not None else None
            ),
            "final_answer": self.final_answer,
        }


def stable_tool_call_id(*, run_id: str, step_index: int) -> str:
    """按 run_id 与步骤序号生成稳定的工具调用标识。

    该标识不依赖 LLM 返回的随机 `call_id`，因此恢复重放同一个 pending action 时
    下游可以用它作为幂等键。
    """
    if not run_id:
        raise CheckpointValidationError("run_id cannot be empty")
    if step_index < 0:
        raise CheckpointValidationError("step_index cannot be negative")
    return f"{run_id}:{step_index}"


def encode_checkpoint(
    checkpoint: AgentCheckpoint,
    *,
    max_bytes: int = DEFAULT_CHECKPOINT_MAX_BYTES,
) -> dict[str, Any]:
    """校验并编码 checkpoint 为 JSON 兼容载荷。

    Raises:
        CheckpointValidationError: checkpoint 结构自相矛盾。
        CheckpointSerializationError: 含不可序列化状态或超过 `max_bytes`。
    """
    _validate_checkpoint(checkpoint)
    payload = checkpoint.to_payload()
    try:
        encoded = orjson_dumps_bytes(payload, default=_reject_unknown_type)
    except TypeError as exc:
        raise CheckpointSerializationError(
            f"checkpoint contains non-serializable state: {exc}"
        ) from exc
    except ValueError as exc:
        raise CheckpointSerializationError(
            f"checkpoint encoding failed: {exc}"
        ) from exc

    if len(encoded) > max_bytes:
        raise CheckpointSerializationError(
            f"checkpoint size {len(encoded)} exceeds limit {max_bytes}"
        )
    return payload


def decode_checkpoint(
    payload: Any,
    *,
    max_bytes: int = DEFAULT_CHECKPOINT_MAX_BYTES,
) -> AgentCheckpoint:
    """校验并解码 checkpoint 载荷。

    接受 JSON 文本或已解析的 mapping；任何结构问题都会抛出 `CheckpointError` 子类，
    调用方不得在失败后丢弃状态重跑。

    Raises:
        UnsupportedCheckpointVersionError: schema_version 未知。
        CheckpointValidationError: JSON 无效、字段缺失或结构矛盾。
        CheckpointSerializationError: 载荷超过 `max_bytes`。
    """
    if isinstance(payload, str | bytes | bytearray | memoryview):
        # 先做大小检查，避免为超限载荷付出解析成本。
        if len(payload) > max_bytes:
            raise CheckpointSerializationError(
                f"checkpoint size {len(payload)} exceeds limit {max_bytes}"
            )
        try:
            raw = orjson_loads(payload)
        except ValueError as exc:
            raise CheckpointValidationError(
                "checkpoint payload is not valid JSON"
            ) from exc
    else:
        raw = payload

    if not isinstance(raw, Mapping):
        raise CheckpointValidationError("checkpoint payload must be a JSON object")

    schema_version = raw.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise CheckpointValidationError("checkpoint schema_version must be an integer")
    if schema_version not in SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS:
        raise UnsupportedCheckpointVersionError(
            f"unsupported checkpoint schema_version: {schema_version}"
        )

    steps = _decode_steps(raw.get("steps"))
    pending_action = _decode_optional_action(
        raw.get("pending_action"), field_name="pending_action"
    )
    phase = _decode_phase(raw.get("phase"))

    checkpoint = AgentCheckpoint(
        run_id=_require_non_empty_str(raw, "run_id"),
        phase=phase,
        max_steps=_require_int(raw, "max_steps"),
        user_input=_require_str(raw, "user_input"),
        definition_version=_require_non_empty_str(raw, "definition_version"),
        next_step_index=_require_int(raw, "next_step_index"),
        session_context=_require_mapping(raw, "session_context"),
        route=_optional_str(raw, "route"),
        agent_name=_optional_str(raw, "agent_name"),
        model_config=_require_mapping(raw, "model_config"),
        steps=steps,
        pending_action=pending_action,
        final_answer=_optional_str(raw, "final_answer"),
        revision=_require_int(raw, "revision"),
        schema_version=schema_version,
    )
    _validate_checkpoint(checkpoint)
    return checkpoint


def _validate_checkpoint(checkpoint: AgentCheckpoint) -> None:
    """校验 checkpoint 的阶段、步骤连续性与步数边界。"""
    if not checkpoint.run_id:
        raise CheckpointValidationError("checkpoint run_id cannot be empty")
    if checkpoint.max_steps < 1:
        raise CheckpointValidationError("checkpoint max_steps must be greater than 0")
    if checkpoint.revision < 0:
        raise CheckpointValidationError("checkpoint revision cannot be negative")
    if not checkpoint.definition_version:
        raise CheckpointValidationError("checkpoint definition_version cannot be empty")

    for position, step in enumerate(checkpoint.steps):
        if step.index != position:
            raise CheckpointValidationError(
                "checkpoint steps must be contiguous and ordered by index"
            )
        if step.action_type not in _ACTION_TYPES:
            raise CheckpointValidationError(
                f"checkpoint step {step.index} has unsupported action type"
            )
        if step.action_type == "tool_call" and not step.tool:
            raise CheckpointValidationError(
                f"checkpoint step {step.index} tool_call requires a tool name"
            )

    if checkpoint.next_step_index != len(checkpoint.steps):
        raise CheckpointValidationError(
            "checkpoint next_step_index must equal the number of committed steps"
        )
    if checkpoint.next_step_index > checkpoint.max_steps:
        raise CheckpointValidationError(
            "checkpoint next_step_index cannot exceed max_steps"
        )

    phase = checkpoint.phase
    if phase is AgentRunPhase.FINAL_READY:
        if checkpoint.final_answer is None:
            raise CheckpointValidationError(
                "final_ready checkpoint requires final_answer"
            )
        if checkpoint.pending_action is not None:
            raise CheckpointValidationError(
                "final_ready checkpoint cannot carry pending_action"
            )
        return

    if phase in (AgentRunPhase.BEFORE_TOOL, AgentRunPhase.TOOL_IN_FLIGHT):
        if checkpoint.pending_action is None:
            raise CheckpointValidationError(
                f"{phase.value} checkpoint requires pending_action"
            )
        if checkpoint.pending_action.get("type") != "tool_call":
            raise CheckpointValidationError(
                f"{phase.value} checkpoint requires a tool_call pending_action"
            )
        pending_tool = checkpoint.pending_action.get("tool")
        if not isinstance(pending_tool, str) or not pending_tool:
            raise CheckpointValidationError(
                f"{phase.value} checkpoint requires a pending_action tool name"
            )
        if checkpoint.final_answer is not None:
            raise CheckpointValidationError(
                f"{phase.value} checkpoint cannot carry final_answer"
            )
        return

    if checkpoint.pending_action is not None:
        raise CheckpointValidationError(
            f"{phase.value} checkpoint cannot carry pending_action"
        )
    if checkpoint.final_answer is not None:
        raise CheckpointValidationError(
            f"{phase.value} checkpoint cannot carry final_answer"
        )


def _decode_steps(raw_steps: Any) -> tuple[AgentCheckpointStep, ...]:
    """解析步骤快照列表。"""
    if raw_steps is None:
        return ()
    if not isinstance(raw_steps, Sequence) or isinstance(raw_steps, str | bytes):
        raise CheckpointValidationError("checkpoint steps must be a JSON array")

    steps: list[AgentCheckpointStep] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, Mapping):
            raise CheckpointValidationError("checkpoint step must be a JSON object")
        action = raw_step.get("action")
        if not isinstance(action, Mapping):
            raise CheckpointValidationError(
                "checkpoint step requires a JSON object action"
            )
        action_type = action.get("type")
        if action_type not in _ACTION_TYPES:
            raise CheckpointValidationError(
                f"checkpoint step has unsupported action type: {action_type!r}"
            )
        if action_type == "tool_call" and not _non_empty_str(action.get("tool")):
            raise CheckpointValidationError(
                "checkpoint tool_call step requires a tool name"
            )
        if action_type == "final" and not isinstance(action.get("answer"), str):
            raise CheckpointValidationError(
                "checkpoint final step requires a string answer"
            )
        if action_type == "tool_call":
            _validate_tool_call_identity(
                action, field_name=f"step {action.get('tool')}"
            )
        error = raw_step.get("error")
        if error is not None and not isinstance(error, str):
            raise CheckpointValidationError(
                "checkpoint step error must be a string or null"
            )
        steps.append(
            AgentCheckpointStep(
                index=_require_int(raw_step, "index"),
                status=_require_non_empty_str(raw_step, "status"),
                action=action,
                action_result=raw_step.get("action_result"),
                error=error,
                elapsed_ms=_require_number(raw_step, "elapsed_ms"),
            )
        )
    return tuple(steps)


def _decode_optional_action(
    raw_action: Any, *, field_name: str = "action"
) -> dict[str, Any] | None:
    """解析可选的待执行动作。"""
    if raw_action is None:
        return None
    if not isinstance(raw_action, Mapping):
        raise CheckpointValidationError(
            f"checkpoint {field_name} must be a JSON object"
        )
    action = dict(raw_action)
    action_type = action.get("type")
    if action_type not in _ACTION_TYPES:
        raise CheckpointValidationError(
            f"checkpoint {field_name} has unsupported action type: {action_type!r}"
        )
    if action_type == "tool_call":
        if not _non_empty_str(action.get("tool")):
            raise CheckpointValidationError(
                f"checkpoint {field_name} tool_call requires a tool name"
            )
        args = action.get("args", {})
        if not isinstance(args, Mapping):
            raise CheckpointValidationError(
                f"checkpoint {field_name} tool_call args must be a JSON object"
            )
        _validate_tool_call_identity(action, field_name=field_name)
    elif not isinstance(action.get("answer"), str):
        raise CheckpointValidationError(
            f"checkpoint {field_name} final requires a string answer"
        )
    return action


def _validate_tool_call_identity(action: Mapping[str, Any], *, field_name: str) -> None:
    """校验工具调用标识字段。"""
    call_id = action.get("call_id")
    if call_id is not None and not isinstance(call_id, str):
        raise CheckpointValidationError(
            f"checkpoint {field_name} call_id must be a string or null"
        )
    tool_call_id = action.get("tool_call_id")
    if tool_call_id is not None and not _non_empty_str(tool_call_id):
        raise CheckpointValidationError(
            f"checkpoint {field_name} tool_call_id must be a non-empty string or null"
        )


def _decode_phase(raw_phase: Any) -> AgentRunPhase:
    """解析执行阶段。"""
    if not isinstance(raw_phase, str):
        raise CheckpointValidationError("checkpoint phase must be a string")
    try:
        return AgentRunPhase(raw_phase)
    except ValueError as exc:
        raise CheckpointValidationError(
            f"unsupported checkpoint phase: {raw_phase!r}"
        ) from exc


def _reject_unknown_type(value: Any) -> Any:
    """orjson fallback：拒绝 checkpoint 中不可序列化的 Python 对象。"""
    raise TypeError(f"type {type(value).__name__} is not JSON serializable")


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _require_str(raw: Mapping[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str):
        raise CheckpointValidationError(f"checkpoint {field} must be a string")
    return value


def _require_non_empty_str(raw: Mapping[str, Any], field: str) -> str:
    value = _require_str(raw, field)
    if not value:
        raise CheckpointValidationError(f"checkpoint {field} cannot be empty")
    return value


def _optional_str(raw: Mapping[str, Any], field: str) -> str | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise CheckpointValidationError(f"checkpoint {field} must be a string or null")
    return value


def _require_int(raw: Mapping[str, Any], field: str) -> int:
    value = raw.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise CheckpointValidationError(f"checkpoint {field} must be an integer")
    return value


def _require_number(raw: Mapping[str, Any], field: str) -> float:
    value = raw.get(field, 0)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CheckpointValidationError(f"checkpoint {field} must be a number")
    return float(value)


def _require_mapping(raw: Mapping[str, Any], field: str) -> dict[str, Any]:
    value = raw.get(field)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CheckpointValidationError(f"checkpoint {field} must be a JSON object")
    return dict(value)


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "DEFAULT_CHECKPOINT_MAX_BYTES",
    "SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS",
    "AgentCheckpoint",
    "AgentCheckpointStep",
    "AgentRunPhase",
    "CheckpointError",
    "CheckpointSerializationError",
    "CheckpointValidationError",
    "ToolReplayPolicy",
    "UnsupportedCheckpointVersionError",
    "decode_checkpoint",
    "encode_checkpoint",
    "stable_tool_call_id",
]
