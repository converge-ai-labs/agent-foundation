"""Cooperative model-boundary pauses and explicit, single-use restart handoff."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from a13n_harness import HarnessState
from a13n_harness.context import AgentContext
from a13n_harness.model_context import ModelContextCoordinatorCapability
from anyio import Event, Lock, create_task_group, move_on_after
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, WrapModelRequestHandler
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import ModelRequestContext

from a13n_harness_ui.errors import RunCoordinationError
from a13n_harness_ui.maintenance_models import MaintenanceTask, MaintenanceView, RestartBatch, RestartItem
from a13n_harness_ui.storage.restarts import RestartRepository


@dataclass
class PausingRun:
    thread_id: str
    execution_id: str | None = None
    state: HarnessState | None = None
    release: Event = field(default_factory=Event)


class UpdateMaintenance:
    def __init__(self, repository: RestartRepository, *, enabled: bool) -> None:
        self.repository = repository
        self.enabled = enabled
        self.owner_id = f"app-{uuid4().hex}"
        self.batch: RestartBatch | None = None
        self.requested = False
        self.committing = False
        self.restoring = False
        self.restoration_pending: set[str] = set()
        self.active: dict[str, PausingRun] = {}
        self.finalized: dict[str, RestartItem] = {}
        self.errors: dict[str, str] = {}
        self.restored: dict[str, MaintenanceTask] = {}
        self.successors: dict[str, dict[str, str]] = {}
        self.continued_threads: set[str] = set()
        self.release = Event()
        self.pause_requested = Event()
        self.changed = Event()
        self._lock = Lock()

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
            raise RunCoordinationError("The App is in update maintenance.", code="maintenance_active")

    async def prepare(self) -> MaintenanceView:
        if not self.enabled:
            raise RunCoordinationError("Update maintenance requires WebUI mode.", code="maintenance_unavailable")
        async with self._lock:
            if self.requested:
                return await self.view()
            self.require_input()
            self.requested = True
            self.errors.clear()
            self.finalized.clear()
            self.restored.clear()
            self.release = Event()
            batch = RestartBatch(batch_id=f"restart-{uuid4().hex}", owner_id=self.owner_id, state="preparing")
            try:
                await self.repository.begin(batch)
            except BaseException:
                self.requested = False
                self.release.set()
                raise
            self.batch = batch
            self.pause_requested.set()
            self.signal()
        return await self.view()

    async def cancel(self) -> MaintenanceView:
        async with self._lock:
            if self.committing or self.restoring:
                raise RunCoordinationError("The handoff can no longer be cancelled.", code="maintenance_conflict")
            batch = await self.repository.get()
            if batch is not None:
                if batch.owner_id != self.owner_id and batch.state in {"preparing", "claimed"}:
                    raise RunCoordinationError(
                        "Another instance owns this handoff. Stop that instance before explicitly discarding it.",
                        code="maintenance_conflict",
                    )
                await self.repository.clear(batch)
            self.batch = None
            self.requested = False
            self.pause_requested = Event()
            self.errors.clear()
            self.restored.clear()
            for entry in self.active.values():
                entry.state = None
            self.release.set()
            self.signal()
        return await self.view()

    async def dismiss(self) -> MaintenanceView:
        """Explicitly abandon non-recoverable handoffs; never start any work."""
        async with self._lock:
            if self.requested or self.restoring or self.committing:
                raise RunCoordinationError("This instance still owns active maintenance.", code="maintenance_conflict")
            batch = await self.repository.get()
            if batch is not None:
                # The caller must confirm that the old instance has stopped.
                await self.repository.clear(batch)
            self.batch = None
            self.errors.clear()
            self.restored.clear()
        return await self.view()

    async def checkpoint(self, thread_id: str, state: HarnessState) -> None:
        if not self.requested and thread_id not in self.restoration_pending:
            return
        entry = self.active.get(thread_id)
        if entry is None:
            raise RunCoordinationError("An update Run is not registered.", code="maintenance_run_missing")
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

    def shutdown(self) -> None:
        if not self.requested:
            return
        if self.errors or any(entry.state is None for entry in self.active.values()):
            self.errors["app"] = "The service stopped before every task reached its pause boundary."
        else:
            self.committing = True
        self.signal()

    def saved_state(self, thread_id: str) -> HarnessState | None:
        entry = self.active.get(thread_id)
        return entry.state if self.committing and entry is not None else None

    def saved(self, item: RestartItem) -> None:
        self.finalized[item.thread_id] = item

    async def commit(self) -> None:
        if not self.requested or self.batch is None:
            return
        batch = self.batch.model_copy(
            update={
                "state": "ready" if self.committing and not self.errors else "blocked",
                "items": tuple(self.finalized.values()),
                "error": "; ".join(self.errors.values()) or None,
            }
        )
        await self.repository.replace(self.batch, batch)
        self.batch = batch

    async def view(self) -> MaintenanceView:
        batch = await self.repository.get()
        # A local transition may complete during the database read. Keep its
        # status and flags from the same process-local observation.
        if batch is not None and self.batch is not None and batch.batch_id == self.batch.batch_id:
            batch = self.batch
        phase = "idle"
        message = None
        if self.restoring or (self.enabled and batch is not None and batch.state == "ready"):
            phase = "restoring"
        elif self.requested:
            phase = (
                "stopping"
                if self.committing
                else ("paused" if all(entry.state is not None for entry in self.active.values()) else "draining")
            )
        elif batch is not None:
            phase = "finished" if batch.state == "finished" else "blocked"
            message = batch.error or (
                "An unfinished update handoff exists. Check the previous instance before discarding it."
                if phase == "blocked"
                else "Planned update recovery finished."
            )
        if self.errors or (batch is not None and batch.error):
            phase = "blocked"
            message = "; ".join(self.errors.values()) or (batch.error if batch is not None else None)
        tasks = (
            tuple(
                MaintenanceTask(
                    thread_id=entry.thread_id,
                    execution_id=entry.execution_id,
                    state="paused" if entry.state is not None else "draining",
                )
                for entry in self.active.values()
            )
            if self.requested
            else tuple(self.restored.values()) or (() if batch is None else batch.results)
        )
        return MaintenanceView(
            enabled=self.enabled,
            phase=phase,
            batch_id=None if batch is None else batch.batch_id,
            tasks=tasks,
            message=message,
            can_cancel=self.requested and not self.committing,
            can_dismiss=batch is not None and phase != "restoring" and not (self.requested or self.restoring),
        )


class UpdatePauseCapability(AbstractCapability[AgentContext]):
    """Hold only outer model requests, after canonical context transformations."""

    id = "a13n.harness-ui.update-pause"

    def __init__(self, maintenance: UpdateMaintenance, thread_id: str) -> None:
        self._maintenance = maintenance
        self._thread_id = thread_id
        self._run_id: str | None = None
        self._continued = thread_id in maintenance.continued_threads
        self._successors = dict(maintenance.successors.get(thread_id, {}))

    def get_instructions(self) -> str | None:
        if not self._continued:
            return None
        mapping = "; ".join(f"{old} -> {new}" for old, new in self._successors.items()) or "none"
        return (
            "This task continued after a planned application update. Previous subagent executions have linked "
            f"successors: {mapping}. Inspect/control the successor IDs; do not repeat their delegation. "
            "Previous Run-local shell references are not valid in this Run. Inspect the Environment before "
            "retrying any external operation."
        )

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(wrapped_by=(ModelContextCoordinatorCapability,))

    async def wrap_run(self, ctx: RunContext[AgentContext], *, handler: Callable[[], Awaitable[Any]]) -> Any:
        if self._run_id is not None:
            return await handler()
        self._run_id = ctx.run_id
        try:
            return await handler()
        finally:
            self._run_id = None

    async def wrap_model_request(
        self,
        ctx: RunContext[AgentContext],
        *,
        request_context: ModelRequestContext,
        handler: WrapModelRequestHandler,
    ) -> ModelResponse:
        if ctx.run_id == self._run_id and (self._maintenance.requested or self._maintenance.restoring):
            await self._maintenance.checkpoint(self._thread_id, await ctx.deps.export_state(ctx.messages))
        return await handler(request_context)
