"""受管理 Agent 运行的 runtime 协议。

runtime 是通用执行器与持久化实现之间唯一的契约：

- 通用执行器（`pkg.agents.react`）只通过本协议读写执行现场和检查执行权限；
- 数据库、session、HTTP 和业务概念都不进入执行器；
- 打断检查与 checkpoint 写入必须是同一个原子控制操作，不能先读标志再无条件覆盖状态。

本模块只定义协议和数据结构，不提供数据库实现。没有 runtime 时，执行器保持原有的
纯内存行为。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pkg.agents.react import AgentRunResult, AgentStepRecord
from pkg.agents.state import AgentCheckpoint


class AgentControlStatus(StrEnum):
    """受管理 run 的持久化状态，与 `agent_run.status` 取值一致。"""

    READY = "ready"
    RUNNING = "running"
    INTERRUPT_REQUESTED = "interrupt_requested"
    INTERRUPTED = "interrupted"
    RECOVERY_REQUIRED = "recovery_required"
    COMPLETED = "completed"
    MAX_STEPS_REACHED = "max_steps_reached"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        """终态不允许再恢复为运行中。"""
        return self in _TERMINAL_STATUSES

    @property
    def is_resumable(self) -> bool:
        """只有 ready 与 interrupted 允许普通恢复。"""
        return self in (AgentControlStatus.READY, AgentControlStatus.INTERRUPTED)


_TERMINAL_STATUSES = frozenset(
    {
        AgentControlStatus.COMPLETED,
        AgentControlStatus.MAX_STEPS_REACHED,
        AgentControlStatus.FAILED,
        AgentControlStatus.RECOVERY_REQUIRED,
    }
)


class AgentControlStopReason(StrEnum):
    """执行器在安全点停止的原因。"""

    INTERRUPT_REQUESTED = "interrupt_requested"
    LEASE_LOST = "lease_lost"
    STATE_CONFLICT = "state_conflict"


@dataclass(frozen=True, slots=True)
class AgentControlState:
    """runtime 暴露给执行器的只读控制状态。

    Attributes:
        status: 当前持久化状态。
        revision: 已提交 checkpoint 的 revision。
        attempt_no: 当前执行 attempt 序号；没有 attempt 记录时为 0。
        interrupt_requested: 是否已经持久化过打断请求。
        lease_owned: 当前执行者是否仍持有有效 lease。
        stop_reason: 状态本身给出的停止原因，例如 lease 失效。
    """

    status: AgentControlStatus
    revision: int = 0
    attempt_no: int = 0
    interrupt_requested: bool = False
    lease_owned: bool = True
    stop_reason: AgentControlStopReason | None = None

    @property
    def should_stop(self) -> bool:
        """当前状态是否要求执行者不再启动新的动作或工具调用。"""
        if not self.lease_owned or self.interrupt_requested:
            return True
        if self.status in (
            AgentControlStatus.INTERRUPT_REQUESTED,
            AgentControlStatus.INTERRUPTED,
            AgentControlStatus.RECOVERY_REQUIRED,
        ):
            return True
        return self.status.is_terminal


@dataclass(frozen=True, slots=True)
class AgentCheckpointCommit:
    """一次 checkpoint / 步骤提交的结果。

    Attributes:
        checkpoint: 提交成功后的 checkpoint（含新 revision）。
        paused: 执行器必须停止在安全点，不再启动新动作。
        stop_reason: 停止原因；`paused` 为 True 时必定有值。
    """

    checkpoint: AgentCheckpoint
    paused: bool = False
    stop_reason: AgentControlStopReason | None = None


class AgentRunRuntime(Protocol):
    """通用执行器需要的持久化与权限检查操作。

    实现方负责用户归属校验、事务边界、lease/fence 和 revision 乐观并发；
    执行器只按返回值决定是否继续。
    """

    async def load_checkpoint(self) -> AgentCheckpoint | None:
        """读取当前有效 checkpoint；没有受管理现场时返回 None。"""
        ...

    async def load_control_state(self) -> AgentControlState:
        """读取当前控制状态，用于在调用 LLM 或工具前决定是否继续。"""
        ...

    async def commit_checkpoint(
        self,
        *,
        checkpoint: AgentCheckpoint,
        pause_when_interrupted: bool = False,
    ) -> AgentCheckpointCommit:
        """原子提交新的执行现场。

        提交时必须校验 fencing token 与 `checkpoint.revision` 乐观并发版本；
        `pause_when_interrupted` 为 True 时，如果存在未处理的打断请求，实现必须在
        同一事务内把 run 转为 `interrupted` 并返回 `paused=True`，而不是允许调用方
        继续覆盖 run.status。
        """
        ...

    async def commit_step(
        self,
        *,
        step: AgentStepRecord,
        checkpoint: AgentCheckpoint,
        pause_when_interrupted: bool = False,
    ) -> AgentCheckpointCommit:
        """在同一事务中提交已完成步骤与更新后的运行现场。

        `pause_when_interrupted` 为 True 时，若存在未处理的打断请求，实现必须在
        同一事务内把 run 转为 `interrupted` 并释放 lease；已提交的步骤仍然有效。
        """
        ...

    async def commit_terminal(
        self,
        *,
        checkpoint: AgentCheckpoint,
        result: AgentRunResult,
        pause_when_interrupted: bool = False,
    ) -> AgentCheckpointCommit:
        """原子提交终态、final step 与唯一 assistant 消息。

        `pause_when_interrupted` 为 True 且存在未处理的打断请求时，实现必须放弃终态提交，
        改为持久化 `final_ready` 现场并把 run 转为 `interrupted`；恢复时不再调用模型，
        直接提交该 final。终态与 interrupt 请求在同一 run 行上串行化，谁先提交谁生效。
        """
        ...


__all__ = [
    "AgentCheckpointCommit",
    "AgentControlState",
    "AgentControlStatus",
    "AgentControlStopReason",
    "AgentRunRuntime",
]
