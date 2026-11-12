"""受管理 run 的持久化 runtime 适配，持有本次 attempt 的 fencing token。"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from internal.schemas.agent import (
    AgentRunCommitDTO,
    AgentRunResultDTO,
    AgentStepDTO,
)
from internal.services.agents.storage import (
    DatabaseAgentStorageBackend,
)
from pkg.agents import (
    AgentCheckpoint,
    AgentCheckpointCommit,
    AgentControlState,
    AgentControlStatus,
    AgentControlStopReason,
    AgentRunPhase,
    AgentRunResult,
    AgentStepRecord,
    stable_tool_call_id,
    step_record_to_checkpoint_step,
)


class DatabaseAgentRunRuntime:
    """把存储后端适配为 `AgentRunRuntime`，并持有本次 attempt 的 fencing token。"""

    def __init__(
        self,
        *,
        storage: DatabaseAgentStorageBackend,
        user_id: UUID,
        run_id: str,
        lease_token: str,
        checkpoint: AgentCheckpoint,
    ):
        self._storage = storage
        self._user_id = user_id
        self._run_id = run_id
        self._lease_token = lease_token
        self._checkpoint = checkpoint
        self._revision = checkpoint.revision

    @property
    def checkpoint(self) -> AgentCheckpoint:
        """最后一次提交的 checkpoint。"""
        return self._checkpoint

    async def load_checkpoint(self) -> AgentCheckpoint | None:
        """返回本次 attempt 当前的执行现场。"""
        return self._checkpoint

    async def load_control_state(self) -> AgentControlState:
        """读取打断意图、lease 归属与 checkpoint 版本。"""
        control = await self._storage.read_managed_control_state(
            user_id=self._user_id,
            run_id=self._run_id,
            lease_token=self._lease_token,
        )
        return AgentControlState(
            status=AgentControlStatus(control.status),
            revision=control.revision,
            attempt_no=control.attempt_no,
            interrupt_requested=control.interrupt_requested,
            lease_owned=control.lease_owned,
            stop_reason=(
                None if control.lease_owned else AgentControlStopReason.LEASE_LOST
            ),
        )

    async def commit_checkpoint(
        self,
        *,
        checkpoint: AgentCheckpoint,
        pause_when_interrupted: bool = False,
    ) -> AgentCheckpointCommit:
        """提交新的阶段/待执行动作；存在打断请求时保留上一个安全现场。"""
        commit = await self._storage.commit_managed_checkpoint(
            user_id=self._user_id,
            run_id=self._run_id,
            lease_token=self._lease_token,
            expected_revision=self._revision,
            committed_checkpoint=checkpoint,
            pause_checkpoint=(
                self._checkpoint.next_revision()
                if checkpoint.phase is AgentRunPhase.TOOL_IN_FLIGHT
                else checkpoint
            ),
            pause_when_interrupted=pause_when_interrupted,
        )
        return self._apply(commit)

    async def commit_step(
        self,
        *,
        step: AgentStepRecord,
        checkpoint: AgentCheckpoint,
        pause_when_interrupted: bool = False,
    ) -> AgentCheckpointCommit:
        """在同一事务中提交已完成步骤与更新后的运行现场。"""
        committed = self._with_step(checkpoint, step)
        commit = await self._storage.commit_managed_step(
            user_id=self._user_id,
            run_id=self._run_id,
            lease_token=self._lease_token,
            expected_revision=self._revision,
            committed_checkpoint=committed,
            pause_checkpoint=committed,
            step=AgentStepDTO.from_step_record(step),
            pause_when_interrupted=pause_when_interrupted,
        )
        return self._apply(commit)

    async def commit_terminal(
        self,
        *,
        checkpoint: AgentCheckpoint,
        result: AgentRunResult,
        pause_when_interrupted: bool = False,
    ) -> AgentCheckpointCommit:
        """提交终态、final step 与唯一 assistant 消息。"""
        committed = self._with_result_steps(checkpoint, result)
        commit = await self._storage.commit_managed_terminal(
            user_id=self._user_id,
            run_id=self._run_id,
            lease_token=self._lease_token,
            expected_revision=self._revision,
            committed_checkpoint=committed,
            pause_checkpoint=checkpoint,
            terminal_status=result.status.value,
            result=AgentRunResultDTO.from_agent_result(result),
            pause_when_interrupted=pause_when_interrupted,
        )
        return self._apply(commit)

    def _apply(self, commit: AgentRunCommitDTO) -> AgentCheckpointCommit:
        """更新本地 revision 并转换停止原因。"""
        self._revision = commit.revision
        self._checkpoint = commit.checkpoint
        return AgentCheckpointCommit(
            checkpoint=commit.checkpoint,
            paused=commit.paused,
            stop_reason=(
                AgentControlStopReason.INTERRUPT_REQUESTED if commit.paused else None
            ),
        )

    def _with_step(
        self, checkpoint: AgentCheckpoint, step: AgentStepRecord
    ) -> AgentCheckpoint:
        """把新完成的步骤追加进 checkpoint 快照。"""
        if step.index < len(checkpoint.steps):
            return checkpoint
        steps = checkpoint.steps + (
            step_record_to_checkpoint_step(
                step,
                tool_call_id=stable_tool_call_id(
                    run_id=self._run_id, step_index=step.index
                ),
            ),
        )
        return replace(checkpoint, steps=steps, next_step_index=len(steps))

    def _with_result_steps(
        self, checkpoint: AgentCheckpoint, result: AgentRunResult
    ) -> AgentCheckpoint:
        """把结果中尚未提交的步骤追加进 checkpoint 快照。"""
        steps = checkpoint.steps
        for step in result.steps:
            if step.index < len(steps):
                continue
            steps = steps + (
                step_record_to_checkpoint_step(
                    step,
                    tool_call_id=stable_tool_call_id(
                        run_id=self._run_id, step_index=step.index
                    ),
                ),
            )
        if len(steps) == len(checkpoint.steps):
            return checkpoint
        return replace(checkpoint, steps=steps, next_step_index=len(steps))
