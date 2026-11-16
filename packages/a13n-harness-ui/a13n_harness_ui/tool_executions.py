"""App-lifetime tool ownership and coalesced completion input delivery."""

from __future__ import annotations

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
from a13n_harness.tool_execution import StoredToolExecution
from a13n_harness.toolsets.process_manager import recover_shell
from a13n_logging import get_logger
from anyio import Lock, sleep
from anyio.abc import TaskGroup

from a13n_harness_ui.composition import ResolvedRunComposition
from a13n_harness_ui.errors import ConfigurationError, EnvironmentLifecycleError, StoreIntegrityError
from a13n_harness_ui.storage.tool_executions import ToolExecutionRepository
from a13n_harness_ui.thread_inbox import ThreadInbox

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
        self.inbox: ThreadInbox | None = None
        self._group: TaskGroup | None = None
        self.changed: Callable[[str], Awaitable[None]] | None = None
        self.settled: Callable[[str], None] | None = None
        self._accepting = True

    def has_pending_work(self, thread_id: str) -> bool:
        """Whether retained native calls still require process-local child authority."""
        return any(record.thread_id == thread_id for record in self.runtime.active)

    def busy_thread_ids(self) -> frozenset[str]:
        return frozenset(record.thread_id for record in self.runtime.active)

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

    def stop(self) -> None:
        self._accepting = False

    def _completed(self, record: ToolExecutionRecord) -> None:
        if self.inbox is not None:
            self.inbox.schedule(record.thread_id)
        if self._group is not None and self.changed is not None:
            self._group.start_soon(self._publish_change, record.thread_id)
        self._notify_settled(record.thread_id)

    async def _publish_change(self, thread_id: str) -> None:
        if self.changed is not None:
            await self.changed(thread_id)

    async def close(self, *, timeout_seconds: float) -> None:
        self.stop()
        pending = await self.runtime.close(timeout_seconds=timeout_seconds, preserve_recoverable=True)
        if pending:
            get_logger(__name__).warning("Joining retained tool cleanup after grace: %s", ", ".join(pending))
        # Storage, MCP clients and Environment collaborators outlive retained scopes.
        await self.runtime.join()
