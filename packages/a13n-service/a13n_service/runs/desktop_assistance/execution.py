"""Durable admission for user-requested suspension, separate from native deferred calls."""

from contextvars import ContextVar
from typing import Any

import anyio
from a13n_harness import AgentContext
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, ValidatedToolArgs, WrapToolExecuteHandler
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ToolFailed
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import lock, transaction
from a13n_service.infra.errors import conflict
from a13n_service.runs.attempts import Lease, lock_lease
from a13n_service.runs.desktop_assistance.tables import DesktopAssistanceRow, DesktopExecutionRow, DesktopTargetRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import Pending
from a13n_service.runs.tables import RunRow

admitted_run: ContextVar[str | None] = ContextVar("desktop_admitted_run", default=None)


async def request(session: AsyncSession, run_id: str) -> DesktopAssistanceRow | None:
    return await session.scalar(
        select(DesktopAssistanceRow)
        .join(DesktopTargetRow, DesktopTargetRow.holder_id == DesktopAssistanceRow.id)
        .where(
            DesktopAssistanceRow.run_id == run_id,
            DesktopAssistanceRow.cause == "user",
            DesktopAssistanceRow.state != "viewing",
        )
    )


async def prepare(runtime: Runtime, lease: Lease, *, supported: bool) -> None:
    async with transaction(runtime.storage) as session:
        await lock_lease(session, lease)
        previous = await lock(session, DesktopExecutionRow, lease.run_id)
        pause = await request(session, lease.run_id)
        if previous is not None:
            if pause is not None and previous.attempt_id != lease.attempt_id:
                raise conflict("run", lease.run_id, "desktop_pause_attempt_lost")
            previous.attempt_id = lease.attempt_id
            previous.supported = supported
        else:
            session.add(DesktopExecutionRow(run_id=lease.run_id, attempt_id=lease.attempt_id, supported=supported))


async def pending(session: AsyncSession, run: RunRow, existing: Pending | None = None) -> Pending | None:
    """Called under the Run lock only at a complete model/deferred boundary."""
    pause = await request(session, run.id)
    if pause is None:
        return existing
    if run.cancel_requested_at is not None:
        raise conflict("run", run.id, "desktop_run_cancelled")
    execution = await session.get(DesktopExecutionRow, run.id)
    if pause.state != "preparing" or execution is None or execution.inflight or execution.uncertain:
        raise conflict("run", run.id, "desktop_execution_unconfirmed")
    return Pending(
        approvals=existing.approvals if existing else (),
        calls=existing.calls if existing else (),
        desktop_control_id=pause.id,
    )


class ExecutionFence(AbstractCapability[AgentContext]):
    """Account every tool, including non-Computer effects, before it can execute."""

    def __init__(self, runtime: Runtime, lease: Lease):
        self.runtime, self.lease = runtime, lease

    async def wrap_tool_execute(
        self,
        ctx: RunContext[AgentContext],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        async with transaction(self.runtime.storage) as session:
            await lock_lease(session, self.lease)
            pause = await request(session, self.lease.run_id)
            if pause is not None:
                raise ToolFailed("Not executed: the user requested desktop control. Replan after handback.")
            observation_required = await session.scalar(
                select(DesktopAssistanceRow.id)
                .join(DesktopTargetRow, DesktopTargetRow.holder_id == DesktopAssistanceRow.id)
                .where(DesktopAssistanceRow.successor_id == self.lease.run_id, DesktopAssistanceRow.state == "consumed")
            )
            if observation_required and call.tool_name not in {"computer_observe", "computer_describe"}:
                raise ToolFailed("Observe the current desktop and replan before performing other operations.")
            execution = await lock(session, DesktopExecutionRow, self.lease.run_id)
            assert execution is not None
            execution.inflight += 1
        token = admitted_run.set(self.lease.run_id)
        settled = False
        try:
            result = await handler(args)
            settled = True
            return result
        except (CallDeferred, ApprovalRequired):
            # A native deferred call is a known wait, preserved in the same pending batch.
            settled = True
            raise
        finally:
            admitted_run.reset(token)
            with anyio.CancelScope(shield=True), anyio.fail_after(5):
                async with transaction(self.runtime.storage) as session:
                    execution = await lock(session, DesktopExecutionRow, self.lease.run_id)
                    assert execution is not None
                    execution.inflight -= 1
                    if not settled:
                        execution.uncertain = True
