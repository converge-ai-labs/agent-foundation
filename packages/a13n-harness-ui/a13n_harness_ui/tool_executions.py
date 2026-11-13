"""App-lifetime tool ownership and coalesced completion input delivery."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING

from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    CodeActStateOwner,
    ToolExecutionRecord,
    ToolExecutionRuntime,
)
from a13n_harness.environment.errors import EnvironmentProviderError, EnvironmentProviderErrorCategory
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.providers import EnvironmentRuntime
from a13n_harness.execution_identity import format_execution_header
from a13n_harness.tool_execution import StoredToolExecution
from a13n_harness.toolsets.process_manager import recover_shell
from a13n_logging import get_logger
from anyio import CancelScope, Lock, sleep
from anyio.abc import TaskGroup
from anyio.lowlevel import checkpoint

from a13n_harness_ui.composition import ResolvedRunComposition
from a13n_harness_ui.errors import ConfigurationError, EnvironmentLifecycleError, StoreIntegrityError
from a13n_harness_ui.storage.tool_executions import ToolExecutionRepository

if TYPE_CHECKING:
    from a13n_harness_ui.environment_runtime import EnvironmentRunService
from a13n_harness_ui.surfaces import ToolExecutionView


def execution_view(record: ToolExecutionRecord) -> ToolExecutionView:
    return ToolExecutionView(
        **record.model_dump(exclude={"error", "sequential"}),
        available_actions=() if record.terminal else ("cancel",),
    )


class ToolExecutions:
    """Hold Harness owners; schedule UI hints on the existing root task group."""

    def __init__(
        self, store: ToolExecutionRepository | None = None, *, environments: EnvironmentRunService | None = None
    ) -> None:
        self.store = store
        self._environments = environments
        self._recovery_lock = Lock()
        self.runtime = ToolExecutionRuntime(store=store, on_completed=self._completed)
        self._states: dict[str, CodeActStateOwner] = {}
        self._pending: OrderedDict[str, set[str]] = OrderedDict()
        self._subagent_inputs: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
        self.deliver_subagent: Callable[[str, str, tuple[str, ...]], Awaitable[bool]] | None = None
        self._scheduled: set[str] = set()
        self._changed: set[str] = set()
        self._offered: dict[str, set[str]] = {}
        self._retry: set[str] = set()
        self._deliveries: dict[str, CancelScope] = {}
        self._group: TaskGroup | None = None
        self.deliver: Callable[[str, str, tuple[str, ...]], Awaitable[bool]] | None = None
        self.changed: Callable[[str], Awaitable[None]] | None = None
        self.settled: Callable[[str], None] | None = None
        self._accepting = True

    def has_pending_work(self, thread_id: str) -> bool:
        """Include hints in flight, whose IDs have already left the pending queue."""
        return (
            thread_id in self._pending
            or thread_id in self._subagent_inputs
            or thread_id in self._scheduled
            or any(record.thread_id == thread_id for record in self.runtime.active)
        )

    def busy_thread_ids(self) -> frozenset[str]:
        """Include completion delivery that can still resume a child lane."""
        return (
            frozenset(self._pending)
            | frozenset(self._subagent_inputs)
            | frozenset(self._scheduled)
            | frozenset(record.thread_id for record in self.runtime.active)
        )

    def _notify_settled(self, thread_id: str) -> None:
        if self.settled is not None:
            self.settled(thread_id)

    def state(self, thread_id: str) -> CodeActStateOwner:
        return self._states.setdefault(thread_id, CodeActStateOwner())

    def clear_state(self, thread_id: str) -> None:
        # Admitted calls retain their original owner; late writes cannot restore
        # values into the fresh namespace selected by explicit context clearing.
        self._states.pop(thread_id, None)

    def start(self, group: TaskGroup) -> None:
        self._group = group

    async def restore(self) -> None:
        await self._restore_pending()

    async def _restore_pending(self) -> None:
        if self.store is None:
            return
        after = ""
        local_pending = set(self._pending)
        while batch := await self.store.pending_threads(after=after):
            for thread_id in batch:
                local_pending.discard(thread_id)
                self.retry(thread_id, refresh=True)
            after = batch[-1]
        # Refresh local offers that another App consumed or expired.
        for thread_id in local_pending:
            self.retry(thread_id, refresh=True)

    async def recover(self) -> None:
        """Claim unfinished calls without admitting a model Run or replaying tools."""
        if self.store is None or self._environments is None:
            return
        async with self._recovery_lock:
            after = ""
            while self._accepting and self.runtime.accepting and (batch := await self.store.active(after=after)):
                for record in batch:
                    after = record.execution_id
                    if not self._accepting:
                        return
                    if any(item.execution_id == after for item in self.runtime.active):
                        if record.cancel_requested:
                            self.runtime.request_cancellation(record.thread_id, after)
                        continue
                    if not await self.store.owners.acquire(after):
                        continue
                    retained = False
                    try:
                        # A previous owner may have completed between enumeration and claim.
                        execution = await self.store.get(record.thread_id, after)
                        if execution is None or execution.record.terminal:
                            continue
                        reference = await self.store.composition(record.thread_id, after)
                        if execution.shell is None or reference is None:
                            terminal = execution.record.model_copy(
                                update={
                                    "status": "lost",
                                    "foreground_released": True,
                                    "completed_at": datetime.now(UTC),
                                    "error": "The owning App exited; this tool cannot be resumed or replayed.",
                                }
                            )
                            await self.store.save(replace(execution, record=terminal))
                            self._completed(terminal)
                            continue
                        try:
                            composition = await self.store.objects.read_model(reference, ResolvedRunComposition)
                            environment = await self._environments.prepare_recovery(
                                composition,
                                execution.shell.mount_name,
                                original_session_id=execution.shell.process.session_id,
                            )
                        except (ConfigurationError, EnvironmentLifecycleError, StoreIntegrityError) as exc:
                            if isinstance(exc, EnvironmentLifecycleError) and exc.code in {
                                "device_unavailable",
                                "device_connections_closed",
                            }:
                                continue
                            terminal = execution.record.model_copy(
                                update={
                                    "status": "failed",
                                    "foreground_released": True,
                                    "completed_at": datetime.now(UTC),
                                    "error": f"Shell recovery failed: {exc.code}",
                                }
                            )
                            await self.store.save(replace(execution, record=terminal))
                            self._completed(terminal)
                            continue
                        if not self._accepting:
                            return
                        self.runtime.resume_shell(
                            execution,
                            partial(self._supervise, execution, environment),
                            release_ownership=partial(self.store.owners.release, after),
                        )
                        retained = True
                    except Exception:
                        get_logger(__name__).warning("Shell recovery deferred: %s", after, exc_info=True)
                    finally:
                        if not retained:
                            await self.store.owners.release(after)

    async def maintain(self) -> None:
        while self._accepting:
            try:
                await self.recover()
                await self._restore_pending()
            except Exception:
                get_logger(__name__).warning("Shell recovery scan failed", exc_info=True)
            await sleep(1)

    async def _supervise(self, execution: StoredToolExecution, environment: EnvironmentRuntime) -> object:
        record = execution.record
        assert execution.shell is not None
        instance = AgentInstanceContext(
            identity=AgentIdentityRef(issuer="a13n-harness-ui", subject=record.thread_id),
            agent_instance_id=f"agent-{record.execution_id}",
        )
        try:
            async with environment.bind(
                thread_id=record.thread_id, run_id=record.run_id, instance=instance, host_refs={}
            ) as bound:
                await environment._activate()
                return await recover_shell(
                    bound, execution.shell, runtime=self.runtime, execution_id=record.execution_id
                )
        except EnvironmentError as exc:
            if exc.code in {"environment_unavailable", "environment_timeout", "environment_busy"}:
                self.runtime.suspend_shell(record.execution_id)
            raise
        except EnvironmentProviderError as exc:
            if exc.category in {EnvironmentProviderErrorCategory.UNAVAILABLE, EnvironmentProviderErrorCategory.TIMEOUT}:
                self.runtime.suspend_shell(record.execution_id)
            raise

    def forget(self, thread_id: str) -> None:
        """Release local delivery bookkeeping when a Thread is deleted."""
        self._pending.pop(thread_id, None)
        self._subagent_inputs.pop(thread_id, None)
        self._offered.pop(thread_id, None)
        if (scope := self._deliveries.get(thread_id)) is not None:
            scope.cancel()
        self._notify_settled(thread_id)

    def stop(self) -> None:
        self._accepting = False
        self._pending.clear()
        self._subagent_inputs.clear()
        for scope in self._deliveries.values():
            scope.cancel()

    def _completed(self, record: ToolExecutionRecord) -> None:
        if not self._accepting or not record.foreground_released:
            self._notify_settled(record.thread_id)
            return
        thread_id = record.thread_id
        self._changed.add(thread_id)
        pending = self._pending.setdefault(thread_id, set())
        if self.store is not None or len(pending) < 256:
            pending.add(record.execution_id)
        # Memory-only owners retain the original bounded hint queue.
        while self.store is None and len(self._pending) > 256:
            evicted, _ = self._pending.popitem(last=False)
            self._notify_settled(evicted)
        self.retry(thread_id, refresh=True)
        self._notify_settled(thread_id)

    def run_settled(self, thread_id: str) -> None:
        self._offered.pop(thread_id, None)
        self.retry(thread_id, refresh=self.store is not None)

    def offer_subagent_input(self, thread_id: str, text: str, execution_ids: tuple[str, ...]) -> None:
        """Use the existing dispatch/retry lane for process-local child inputs."""
        if not self._accepting:
            return
        self._subagent_inputs.setdefault(thread_id, []).append((text, execution_ids))
        self.retry(thread_id)

    def retry(self, thread_id: str, *, refresh: bool = False) -> None:
        if (
            not self._accepting
            or self._group is None
            or (thread_id not in self._pending and thread_id not in self._subagent_inputs and not refresh)
        ):
            return
        if thread_id in self._scheduled:
            self._retry.add(thread_id)
            return
        self._scheduled.add(thread_id)
        self._group.start_soon(self._deliver, thread_id)

    async def _deliver(self, thread_id: str) -> None:
        scope = CancelScope()
        self._deliveries[thread_id] = scope
        execution_ids: set[str] = set()
        try:
            with scope:
                await checkpoint()  # Coalesce completions from the same dispatch batch.
                if thread_id in self._changed:
                    self._changed.discard(thread_id)
                    if self.changed is not None:
                        await self.changed(thread_id)
                notices = self._subagent_inputs.pop(thread_id, [])
                if notices and self.deliver_subagent is not None:
                    text = "\n\n".join(item[0] for item in notices)
                    references = tuple(dict.fromkeys(key for item in notices for key in item[1]))
                    try:
                        accepted = await self.deliver_subagent(thread_id, text, references)
                    except Exception:
                        self._subagent_inputs.setdefault(thread_id, [])[:0] = notices
                        raise
                    if not accepted:
                        self._subagent_inputs.setdefault(thread_id, [])[:0] = notices
                if self.store is not None:
                    # Keep the offer owner through Run settlement, not merely
                    # enqueue. Process death releases the OS lock for takeover.
                    if not await self.store.completion_owners.acquire(thread_id):
                        return
                    records = await self.store.pending(thread_id, exclude=tuple(self._offered.get(thread_id, ())))
                    pending = {record.execution_id for record in records}
                    if pending:
                        self._pending[thread_id] = pending
                    else:
                        self._pending.pop(thread_id, None)
                if thread_id not in self._pending or self.deliver is None:
                    return
                # Pop before awaiting: racing completions receive their own hint.
                execution_ids = self._pending.pop(thread_id)
                records = [await self.runtime.info(thread_id, key) for key in sorted(execution_ids)]
                completed = [record for record in records if record is not None and not record.delivery_acknowledged]
                if not completed:
                    return
                prompt = "Tool executions completed. Inspect or retrieve their retained results:\n" + "\n".join(
                    format_execution_header(
                        "Tool notification",
                        record.tool_name,
                        record.execution_id,
                        "finished" if record.status == "succeeded" else record.status,
                    )
                    for record in completed
                )
                self._offered.setdefault(thread_id, set()).update(execution_ids)
                if not await self.deliver(thread_id, prompt, tuple(record.execution_id for record in completed)):
                    self._offered.get(thread_id, set()).difference_update(execution_ids)
                    self._pending.setdefault(thread_id, set()).update(execution_ids)
                elif self.store is not None:
                    self.retry(thread_id, refresh=True)
        except Exception:
            self._offered.get(thread_id, set()).difference_update(execution_ids)
            if execution_ids:
                self._pending.setdefault(thread_id, set()).update(execution_ids)
            get_logger(__name__).warning("Could not deliver tool completion hint: %s", thread_id, exc_info=True)
        finally:
            if self.store is not None and not self._offered.get(thread_id):
                await self.store.completion_owners.release(thread_id)
            self._deliveries.pop(thread_id, None)
            self._scheduled.discard(thread_id)
            if thread_id in self._retry:
                self._retry.discard(thread_id)
                self.retry(thread_id, refresh=self.store is not None)
            self._notify_settled(thread_id)

    async def close(self, *, timeout_seconds: float) -> None:
        self.stop()
        pending = await self.runtime.close(timeout_seconds=timeout_seconds, preserve_recoverable=True)
        if pending:
            get_logger(__name__).warning("Joining retained tool cleanup after grace: %s", ", ".join(pending))
        # Storage, MCP clients and Environment collaborators outlive retained scopes.
        await self.runtime.join()
        if self.store is not None:
            await self.store.completion_owners.close()
