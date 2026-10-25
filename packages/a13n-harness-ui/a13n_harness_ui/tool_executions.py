"""App-lifetime tool ownership and coalesced completion input delivery."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Awaitable, Callable

from a13n_harness import CodeActStateOwner, ToolExecutionRecord, ToolExecutionRuntime
from a13n_logging import get_logger
from anyio import CancelScope
from anyio.abc import TaskGroup
from anyio.lowlevel import checkpoint

from a13n_harness_ui.surfaces import ToolExecutionView


def execution_view(record: ToolExecutionRecord) -> ToolExecutionView:
    return ToolExecutionView(
        **record.model_dump(exclude={"error"}),
        available_actions=() if record.terminal else ("cancel",),
    )


class ToolExecutions:
    """Hold Harness owners; schedule UI hints on the existing root task group."""

    def __init__(self) -> None:
        self.runtime = ToolExecutionRuntime(on_completed=self._completed)
        self._states: dict[str, CodeActStateOwner] = {}
        self._pending: OrderedDict[str, set[str]] = OrderedDict()
        self._suppressed: set[str] = set()
        self._scheduled: set[str] = set()
        self._retry: set[str] = set()
        self._deliveries: dict[str, CancelScope] = {}
        self._group: TaskGroup | None = None
        self.deliver: Callable[[str, str], Awaitable[bool]] | None = None
        self.changed: Callable[[str], Awaitable[None]] | None = None
        self.settled: Callable[[str], None] | None = None
        self._accepting = True

    def has_pending_work(self, thread_id: str) -> bool:
        """Include hints in flight, whose IDs have already left the pending queue."""
        return (
            thread_id in self._pending
            or thread_id in self._scheduled
            or any(record.thread_id == thread_id for record in self.runtime.active)
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

    def suppress(self, thread_id: str) -> None:
        """Stop automatic wake before cancelling the conversation."""
        self._suppressed.add(thread_id)
        self._pending.pop(thread_id, None)
        if (scope := self._deliveries.get(thread_id)) is not None:
            scope.cancel()
        self._notify_settled(thread_id)

    def user_input(self, thread_id: str) -> None:
        self._suppressed.discard(thread_id)
        self.retry(thread_id)

    def stop(self) -> None:
        self._accepting = False
        self._pending.clear()
        for scope in self._deliveries.values():
            scope.cancel()

    def _completed(self, record: ToolExecutionRecord) -> None:
        if not self._accepting or not record.foreground_released:
            self._notify_settled(record.thread_id)
            return
        thread_id = record.thread_id
        if thread_id not in self._suppressed:
            pending = self._pending.setdefault(thread_id, set())
            if len(pending) < 256:
                pending.add(record.execution_id)
            # Hints are best effort; the runtime summary remains authoritative.
            while len(self._pending) > 256:
                evicted, _ = self._pending.popitem(last=False)
                self._notify_settled(evicted)
        self.retry(thread_id, refresh=True)
        self._notify_settled(thread_id)

    def retry(self, thread_id: str, *, refresh: bool = False) -> None:
        if not self._accepting or self._group is None or (thread_id not in self._pending and not refresh):
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
                if self.changed is not None:
                    await self.changed(thread_id)
                if thread_id not in self._pending or thread_id in self._suppressed or self.deliver is None:
                    return
                # Pop before awaiting: racing completions receive their own hint.
                execution_ids = self._pending.pop(thread_id)
                records = [await self.runtime.info(thread_id, key) for key in sorted(execution_ids)]
                completed = [record for record in records if record is not None and not record.delivery_acknowledged]
                if not completed:
                    return
                prompt = "Tool executions completed. Inspect or retrieve their retained results:\n" + "\n".join(
                    f"- {record.execution_id}: {record.tool_name} ({record.status})" for record in completed
                )
                if not await self.deliver(thread_id, prompt):
                    self._pending.setdefault(thread_id, set()).update(execution_ids)
        except Exception:
            self._pending.setdefault(thread_id, set()).update(execution_ids)
            get_logger(__name__).warning("Could not deliver tool completion hint: %s", thread_id, exc_info=True)
        finally:
            self._deliveries.pop(thread_id, None)
            self._scheduled.discard(thread_id)
            if thread_id in self._retry:
                self._retry.discard(thread_id)
                self.retry(thread_id)
            self._notify_settled(thread_id)

    async def close(self, *, timeout_seconds: float) -> None:
        self.stop()
        await self.runtime.close(timeout_seconds=timeout_seconds)
        # Storage, MCP clients and Environment collaborators outlive retained scopes.
        await self.runtime.join()
