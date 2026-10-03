"""Cooperative model-boundary drain for a normal WebUI shutdown."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from a13n_harness import HarnessState
from a13n_harness.context import AgentContext
from a13n_harness.model_context import ModelContextCoordinatorCapability
from a13n_logging import get_logger
from anyio import Event, create_task_group, move_on_after
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.models import ModelRequestContext

from a13n_harness_ui.errors import RunCoordinationError
from a13n_harness_ui.restart_models import RestartBatch, RestartItem
from a13n_harness_ui.storage.restarts import RestartRepository


@dataclass
class PausingRun:
    thread_id: str
    execution_id: str | None = None
    state: HarnessState | None = None
    release: Event = field(default_factory=Event)


class GracefulRestart:
    def __init__(self, repository: RestartRepository, *, enabled: bool) -> None:
        self.repository = repository
        self.enabled = enabled
        self.requested = False
        self.committing = False
        self.restoring = False
        self.restoration_pending: set[str] = set()
        self.active: dict[str, PausingRun] = {}
        self.finalized: dict[str, RestartItem] = {}
        self.errors: dict[str, str] = {}
        self.successors: dict[str, dict[str, str]] = {}
        self.continued_threads: set[str] = set()
        self.release = Event()
        self.pause_requested = Event()
        self.changed = Event()

    def signal(self) -> None:
        self.changed.set()
        self.changed = Event()

    def register(self, thread_id: str, execution_id: str | None = None) -> None:
        self.active[thread_id] = PausingRun(thread_id, execution_id)
        self.signal()

    def finished(self, thread_id: str, *, failed: bool = False) -> None:
        entry = self.active.pop(thread_id, None)
        if self.committing and entry is not None and entry.state is not None and thread_id not in self.finalized:
            self.errors[thread_id] = "Final checkpoint or Environment cleanup failed."
        if failed and (self.requested or self.restoring):
            self.errors[thread_id] = "Execution did not reach a saved continuation boundary."
        self.signal()

    def require_input(self) -> None:
        if self.requested or self.restoring:
            raise RunCoordinationError("The App is stopping or restoring saved work.", code="app_stopping")

    async def drain(self, timeout_seconds: float) -> None:
        """Request safe boundaries while already admitted tool batches finish."""
        if not self.enabled:
            return
        self.requested = True
        self.errors.clear()
        self.finalized.clear()
        self.pause_requested.set()
        self.signal()
        with move_on_after(timeout_seconds):
            while any(entry.state is None for entry in self.active.values()):
                changed = self.changed
                await changed.wait()
        if self.errors or any(entry.state is None for entry in self.active.values()):
            self.errors["app"] = "Shutdown did not reach every continuation boundary; automatic recovery is skipped."
            get_logger(__name__).warning(self.errors["app"])
        else:
            self.committing = True
        self.signal()

    async def checkpoint(self, thread_id: str, state: HarnessState) -> None:
        if not self.requested and thread_id not in self.restoration_pending:
            return
        entry = self.active.get(thread_id)
        if entry is None:
            raise RunCoordinationError("A restart Run is not registered.", code="restart_run_missing")
        while self.requested or thread_id in self.restoration_pending:
            entry.state = state
            self.signal()
            await (entry.release if self.restoring else self.release).wait()
            entry.state = None

    async def wait_child(self, event: Event, timeout: float) -> None:
        if self.requested:
            return

        async def wake(source: Event, done: Event) -> None:
            await source.wait()
            done.set()

        done = Event()
        with move_on_after(timeout):
            async with create_task_group() as group:
                group.start_soon(wake, event, done)
                group.start_soon(wake, self.pause_requested, done)
                await done.wait()
                group.cancel_scope.cancel()

    def saved_state(self, thread_id: str) -> HarnessState | None:
        entry = self.active.get(thread_id)
        return entry.state if self.committing and entry is not None else None

    def saved(self, item: RestartItem) -> None:
        self.finalized[item.thread_id] = item

    async def commit(self) -> None:
        # Publishing is the last shutdown step, after all state and Environment
        # finalization. A failed drain never authorizes replay of a partial batch.
        if not self.requested:
            return
        if not self.committing or self.errors:
            get_logger(__name__).warning(
                "Shutdown recovery was not saved", extra={"errors": tuple(self.errors.values())}
            )
            return
        if self.finalized:
            await self.repository.publish(
                RestartBatch(batch_id=f"restart-{uuid4().hex}", state="ready", items=tuple(self.finalized.values()))
            )


class RestartPauseCapability(AbstractCapability[AgentContext]):
    """Hold only outer model requests, after canonical context transformations."""

    id = "a13n.harness-ui.restart-pause"

    def __init__(self, restart_coordinator: GracefulRestart, thread_id: str) -> None:
        self._restart = restart_coordinator
        self._thread_id = thread_id
        self._run_id: str | None = None
        self._continued = thread_id in restart_coordinator.continued_threads
        self._successors = dict(restart_coordinator.successors.get(thread_id, {}))

    def get_instructions(self) -> str | None:
        if not self._continued:
            return None
        mapping = "; ".join(f"{old} -> {new}" for old, new in self._successors.items()) or "none"
        return (
            "This task continued after a graceful application restart. Previous subagent executions have linked "
            f"successors: {mapping}. Inspect/control the successor IDs; do not repeat their delegation. "
            "Previous Run-local shell references are not valid in this Run. Inspect the Environment before "
            "retrying any external operation."
        )

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost", wrapped_by=(ModelContextCoordinatorCapability,))

    async def wrap_run(self, ctx: RunContext[AgentContext], *, handler: Callable[[], Awaitable[Any]]) -> Any:
        if self._run_id is not None:
            return await handler()
        self._run_id = ctx.run_id
        try:
            return await handler()
        finally:
            self._run_id = None

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        if ctx.run_id == self._run_id and (self._restart.requested or self._restart.restoring):
            await self._restart.checkpoint(self._thread_id, await ctx.deps.export_state(ctx.messages))
        return request_context
