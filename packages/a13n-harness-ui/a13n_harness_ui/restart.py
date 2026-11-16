"""Host-owned safe model boundaries and cooperative WebUI restart handoffs."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from a13n_harness import HarnessState
from a13n_harness.context import AgentContext
from a13n_harness.model_context import ModelContextCoordinatorCapability
from a13n_logging import get_logger
from anyio import Event, Lock, create_task_group, move_on_after
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.models import ModelRequestContext

from a13n_harness_ui.errors import RunCoordinationError
from a13n_harness_ui.restart_models import RestartBatch, RestartItem
from a13n_harness_ui.shutdown import Shutdown
from a13n_harness_ui.storage.restarts import RestartRepository


@dataclass
class PausingRun:
    thread_id: str
    execution_id: str | None = None
    boundary: RestartItem | None = None
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
        self.families: dict[str, str] = {}
        self._candidates: dict[str, RestartItem] = {}
        self._suppressed: set[str] = set()
        self._candidate_lock = Lock()
        self._watch_id = f"restart-{uuid4().hex}"

    def signal(self) -> None:
        self.changed.set()
        self.changed = Event()

    def register(self, thread_id: str, execution_id: str | None = None, *, root_thread_id: str | None = None) -> None:
        self.active[thread_id] = PausingRun(thread_id, execution_id)
        self.families[thread_id] = root_thread_id or thread_id
        self._suppressed.discard(thread_id)
        self.signal()

    async def _publish_candidates(self) -> None:
        # The WebUI automation ownership lock fences this writer across Apps;
        # the local lock serializes concurrent root checkpoint/response hooks.
        previous = await self.repository.get()
        await self.repository.publish(
            RestartBatch(
                batch_id=self._watch_id,
                state="watching",
                items=tuple(self._candidates.values()),
                results=previous.results if previous is not None else (),
            )
        )

    async def boundary(self, item: RestartItem) -> None:
        """Arm a saved root request; child executions retain separate ownership."""
        if not self.enabled:
            return
        async with self._candidate_lock:
            entry = self.active.get(item.thread_id)
            if entry is None:
                return
            entry.boundary = item
            if not self.restoring and item.thread_id not in self._suppressed:
                previous = self._candidates.get(item.thread_id)
                self._candidates[item.thread_id] = item
                try:
                    await self._publish_candidates()
                except BaseException:
                    if previous is None:
                        self._candidates.pop(item.thread_id, None)
                    else:
                        self._candidates[item.thread_id] = previous
                    raise

    async def invalidate(self, thread_id: str, *, suppress: bool = False) -> None:
        """Withdraw before response processing, explicit Stop, or terminal retirement."""
        if not self.enabled:
            return
        async with self._candidate_lock:
            if suppress:
                self._suppressed.add(thread_id)
            previous = self._candidates.pop(thread_id, None)
            if previous is not None:
                try:
                    await self._publish_candidates()
                except BaseException:
                    self._candidates[thread_id] = previous
                    raise

    async def arm_restored(self, *, blocked_roots: set[str]) -> None:
        async with self._candidate_lock:
            for thread_id, entry in self.active.items():
                if (
                    thread_id not in blocked_roots
                    and entry.execution_id is None
                    and entry.boundary is not None
                    and thread_id not in self._suppressed
                ):
                    self._candidates[thread_id] = entry.boundary
            if self._candidates:
                await self._publish_candidates()

    def finished(self, thread_id: str, *, failed: bool = False) -> None:
        entry = self.active.pop(thread_id, None)
        if (
            self.committing
            and entry is not None
            and (entry.state is not None or thread_id in self._candidates)
            and thread_id not in self.finalized
        ):
            self.errors[thread_id] = "Final checkpoint or Environment cleanup failed."
        if failed and (self.requested or self.restoring):
            self.errors[thread_id] = "Execution did not reach a saved continuation boundary."
        self.signal()

    def require_input(self) -> None:
        if self.requested or self.restoring:
            raise RunCoordinationError("The App is stopping or restoring saved work.", code="app_stopping")

    async def drain(self, timeout_seconds: float, *, shutdown: Shutdown | None = None) -> None:
        """Request safe boundaries while already admitted tool batches finish."""
        if not self.enabled:
            return
        self.requested = True
        self.errors.clear()
        self.finalized.clear()
        self.pause_requested.set()
        self.signal()
        with shutdown.grace() if shutdown is not None else move_on_after(timeout_seconds):
            while any(entry.state is None for entry in self.active.values()):
                changed = self.changed
                await changed.wait()
        for thread_id, entry in self.active.items():
            if entry.state is None and thread_id not in self._candidates:
                self.errors[thread_id] = "Shutdown did not reach this task's continuation boundary."
        # Save each complete family independently. A slow unrelated task must
        # not discard the boundaries reached by the rest of the workbench.
        self.committing = True
        if self.errors:
            get_logger(__name__).warning("Some task families did not reach restart boundaries")
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

    def interrupted_boundary(self, thread_id: str) -> RestartItem | None:
        # Called only after the old model Run has joined. A still-armed candidate
        # proves no response processing or tool effect followed this checkpoint.
        return self._candidates.get(thread_id) if self.committing else None

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
        if not self.committing:
            return
        blocked = {self.families.get(thread_id, thread_id) for thread_id in self.errors}
        items = tuple(item for item in self.finalized.values() if item.root_thread_id not in blocked)
        if items:
            await self.repository.publish(RestartBatch(batch_id=f"restart-{uuid4().hex}", state="ready", items=items))
        if self.errors:
            get_logger(__name__).warning(
                "Some task families were not saved for restart", extra={"errors": tuple(self.errors.values())}
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
            "This task continued from a saved application-restart boundary. Previous subagent executions have linked "
            f"successors: {mapping}. Inspect/control the successor IDs; do not repeat their delegation. "
            "Previous child executions without a listed successor were not restarted. A saved running status "
            "does not prove local ownership; inspect retained results and uncertain effects before retrying work. "
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
