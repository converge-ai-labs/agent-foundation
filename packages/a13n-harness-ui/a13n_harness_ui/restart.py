"""Host-owned safe model boundaries and cooperative WebUI restart handoffs."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from a13n_harness import HarnessState
from a13n_harness.context import AgentContext
from a13n_harness.model_context import ModelContextCoordinatorCapability
from a13n_logging import get_logger
from anyio import CancelScope, Event, Lock, create_task_group, move_on_after
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.models import ModelRequestContext

from a13n_harness_ui.errors import RunCoordinationError
from a13n_harness_ui.restart_models import RestartBatch, RestartItem, RestartResult
from a13n_harness_ui.shutdown import Shutdown
from a13n_harness_ui.storage.restarts import RestartRepository


@dataclass
class PausingRun:
    thread_id: str
    execution_id: str | None = None
    boundary: RestartItem | None = None
    state: HarnessState | None = None
    release: Event = field(default_factory=Event)


@dataclass
class _CandidateOp:
    kind: Literal["boundary", "invalidate"]
    thread_id: str
    item: RestartItem | None = None
    cancelled: bool = False


@dataclass
class _CandidateBatch:
    ops: list[_CandidateOp] = field(default_factory=list)
    completed: bool = False


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
        self._next_batch: _CandidateBatch | None = None
        self._watch_id = f"restart-{uuid4().hex}"
        self._cached_results: tuple[RestartResult, ...] | None = None

    def signal(self) -> None:
        self.changed.set()
        self.changed = Event()

    def register(self, thread_id: str, execution_id: str | None = None, *, root_thread_id: str | None = None) -> None:
        self.active[thread_id] = PausingRun(thread_id, execution_id)
        self.families[thread_id] = root_thread_id or thread_id
        self._suppressed.discard(thread_id)
        self.signal()

    def can_pause(self, thread_id: str) -> bool:
        return thread_id in self.active and thread_id not in self._suppressed

    async def _publish_candidates(self) -> None:
        # The WebUI automation ownership lock fences this writer across Apps;
        # the local lock serializes concurrent root checkpoint/response hooks.
        if self._cached_results is None:
            previous = await self.repository.get()
            self._cached_results = previous.results if previous is not None else ()
        await self.repository.publish(
            RestartBatch(
                batch_id=self._watch_id,
                state="watching",
                items=tuple(self._candidates.values()),
                results=self._cached_results,
            )
        )

    def _compute_batch_state(
        self,
        ops: list[_CandidateOp],
        base_candidates: dict[str, RestartItem],
        base_boundaries: dict[str, RestartItem | None],
    ) -> tuple[dict[str, RestartItem], dict[str, RestartItem | None]]:
        candidates = dict(base_candidates)
        boundaries = dict(base_boundaries)
        for pending in ops:
            if pending.cancelled:
                continue
            if pending.kind == "boundary" and pending.item is not None:
                if pending.thread_id in self.active:
                    boundaries[pending.thread_id] = pending.item
                    if not self.restoring and pending.thread_id not in self._suppressed:
                        candidates[pending.thread_id] = pending.item
            elif pending.kind == "invalidate":
                candidates.pop(pending.thread_id, None)
        return candidates, boundaries

    def _restore_state(
        self,
        candidates: dict[str, RestartItem],
        boundaries: dict[str, RestartItem | None],
    ) -> None:
        self._candidates = dict(candidates)
        for tid, entry in self.active.items():
            if tid in boundaries:
                entry.boundary = boundaries[tid]

    async def _commit_candidate_op(self, op: _CandidateOp) -> None:
        batch = self._next_batch
        if batch is None:
            batch = _CandidateBatch()
            self._next_batch = batch
        batch.ops.append(op)
        leader_committed = False
        try:
            async with self._candidate_lock:
                if batch.completed:
                    return
                if self._next_batch is batch:
                    self._next_batch = None
                base_candidates = dict(self._candidates)
                base_boundaries = {tid: entry.boundary for tid, entry in self.active.items()}
                applied_ops = [pending for pending in batch.ops if not pending.cancelled]
                next_candidates, next_boundaries = self._compute_batch_state(
                    applied_ops, base_candidates, base_boundaries
                )
                self._restore_state(next_candidates, next_boundaries)
                if next_candidates != base_candidates:
                    try:
                        await self._publish_candidates()
                    except BaseException:
                        self._restore_state(base_candidates, base_boundaries)
                        raise
                batch.completed = True
                leader_committed = True
                if any(pending.cancelled for pending in applied_ops):
                    reconciled_candidates, reconciled_boundaries = self._compute_batch_state(
                        applied_ops, base_candidates, base_boundaries
                    )
                    self._restore_state(reconciled_candidates, reconciled_boundaries)
                    if reconciled_candidates != next_candidates:
                        with CancelScope(shield=True):
                            await self._publish_candidates()
        except BaseException:
            if not leader_committed:
                op.cancelled = True
                if not batch.completed and self._next_batch is batch and all(item.cancelled for item in batch.ops):
                    self._next_batch = None
            raise

    async def boundary(self, item: RestartItem) -> None:
        """Arm a saved root request; child executions retain separate ownership."""
        if not self.enabled:
            return
        await self._commit_candidate_op(_CandidateOp(kind="boundary", thread_id=item.thread_id, item=item))

    async def invalidate(self, thread_id: str, *, suppress: bool = False) -> None:
        """Withdraw before response processing, explicit Stop, or terminal retirement."""
        if not self.enabled:
            return
        # Record explicit Stop before the request can be cancelled while
        # waiting to withdraw the durable process-loss candidate.
        if suppress:
            self._suppressed.add(thread_id)
        await self._commit_candidate_op(_CandidateOp(kind="invalidate", thread_id=thread_id))

    async def arm_restored(self, *, blocked_roots: set[str]) -> None:
        async with self._candidate_lock:
            self._cached_results = None
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
            and thread_id not in self._suppressed
            and thread_id not in self.finalized
        ):
            self.errors[thread_id] = "Final checkpoint or Environment cleanup failed."
        if failed and entry is not None and thread_id not in self._suppressed and (self.requested or self.restoring):
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
            if entry.state is None and thread_id not in self._candidates and thread_id not in self._suppressed:
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
        return self._candidates.get(thread_id) if self.committing and thread_id not in self._suppressed else None

    def saved_state(self, thread_id: str) -> HarnessState | None:
        if not self.committing or thread_id in self._suppressed:
            return None
        entry = self.active.get(thread_id)
        return entry.state if entry is not None else None

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
        items = tuple(
            item
            for item in self.finalized.values()
            if item.root_thread_id not in blocked and item.thread_id not in self._suppressed
        )
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
