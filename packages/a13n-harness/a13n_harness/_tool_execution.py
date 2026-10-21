"""Logical-Run associations and retained resources for tool execution."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Any

from pydantic_ai import ToolReturn
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from a13n_harness.tool_execution import (
    StoredToolExecution,
    ToolExecutionConfig,
    ToolExecutionRecord,
    ToolExecutionRuntime,
)

TOOL_EXECUTION_METADATA_KEY = "a13n.tool-execution"


class RunToolExecutions:
    """Retain original scopes and settle calls without replaying their effects."""

    def __init__(self) -> None:
        self.config = ToolExecutionConfig()
        self.enabled = False
        self.usage_pending = 0
        self.handoff_requested = asyncio.Event()
        self.unsupported_calls: set[str] = set()
        self.handoff_ready = asyncio.Event()
        self.handoff_ready.set()
        self.calls: dict[str, ToolExecutionRecord] = {}
        self.returns: dict[str, Any] = {}
        self.observed_returns: set[str] = set()
        self.streamed_returns: set[str] = set()
        self.deliveries: dict[str, str] = {}
        self.result_origins: dict[str, ToolExecutionRecord] = {}
        self._leases = 0
        self._drained = asyncio.Event()
        self._drained.set()
        self.closing = False
        self.owner: asyncio.Task[None] | None = None

    def block_handoff(self, call_id: str) -> None:
        self.unsupported_calls.add(call_id)
        self.handoff_ready.clear()

    def unblock_handoff(self, call_id: str) -> None:
        self.unsupported_calls.discard(call_id)
        if not self.unsupported_calls:
            self.handoff_ready.set()

    @property
    def retained(self) -> bool:
        return self._leases > 0

    def retain(self) -> Callable[[], Awaitable[None]]:
        if self.closing:
            raise RuntimeError("Run resource scope no longer admits invocations")
        self._leases += 1
        self._drained.clear()

        async def release() -> None:
            self._leases -= 1
            if not self._leases:
                self._drained.set()
                if self.closing and self.owner is not None:
                    await asyncio.shield(self.owner)

        return release

    async def drain(self) -> None:
        await self._drained.wait()

    async def wait_foreground(
        self, runtime: ToolExecutionRuntime, thread_id: str, execution_id: str, *, automatic: bool = False
    ) -> StoredToolExecution | None:
        """Release only the waiter on handoff; the runtime keeps the operation."""
        if self.handoff_requested.is_set():
            return await runtime.result(thread_id, execution_id)
        waiter = asyncio.create_task(
            runtime.wait(
                thread_id,
                execution_id,
                timeout_seconds=(
                    self.config.foreground_wait_seconds if automatic or self.config.mode == "auto" else None
                ),
            )
        )
        handoff = asyncio.create_task(self.handoff_requested.wait())
        try:
            await asyncio.wait((waiter, handoff), return_when=asyncio.FIRST_COMPLETED)
            if waiter.done():
                result = waiter.result()
                if result is not None and result.record.terminal:
                    return result
        finally:
            waiter.cancel()
            handoff.cancel()
            await asyncio.gather(waiter, handoff, return_exceptions=True)
        # Completion can win while waiter cleanup yields. Read pending state
        # only afterward, keeping receipt settlement synchronous with this view.
        return await runtime.result(thread_id, execution_id)

    def receipt(self, record: ToolExecutionRecord) -> dict[str, Any]:
        value = {
            "type": "tool_execution_receipt",
            "execution_id": record.execution_id,
            "tool_name": record.tool_name,
            "status": record.status,
            "message": "The original invocation is owned by the runtime. Use tool_execution_result or tool_execution_wait; do not reissue it.",
        }
        self.returns[record.tool_call_id] = value
        return value

    def delivered_result(self, call_id: str, record: ToolExecutionRecord, result: Any) -> Any:
        self.returns[call_id] = result
        self.deliveries[call_id] = record.execution_id
        # Stream consumers may observe results after canonical acknowledgement or
        # store eviction. Keep provenance for the lifetime of this Run.
        self.result_origins[call_id] = record
        return result

    def settle(self, messages: Sequence[ModelMessage]) -> tuple[ModelMessage, ...]:
        """Insert owned returns before generic interruption fills missing calls."""
        settled = list(messages)
        present = {
            part.tool_call_id
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart | RetryPromptPart)
        }
        pending = [
            part
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart) and part.tool_call_id in self.returns and part.tool_call_id not in present
        ]
        parts: list[ToolReturnPart | UserPromptPart] = []
        for call in pending:
            value = self.returns[call.tool_call_id]
            if isinstance(value, ToolReturn):
                from a13n_harness.tools._output import _render_tool_return

                value = _render_tool_return(value)
                parts.append(
                    ToolReturnPart(call.tool_name, value.return_value, call.tool_call_id, metadata=value.metadata)
                )
                if value.content:
                    parts.append(UserPromptPart(value.content))
            else:
                parts.append(ToolReturnPart(call.tool_name, value, call.tool_call_id))
        if parts:
            if settled and isinstance(settled[-1], ModelRequest):
                settled[-1] = replace(settled[-1], parts=[*settled[-1].parts, *parts])
            else:
                settled.append(ModelRequest(parts=parts))
        return tuple(settled)

    async def reconcile(
        self, runtime: ToolExecutionRuntime | None, thread_id: str, messages: Sequence[ModelMessage]
    ) -> None:
        if runtime is None:
            return
        for message in messages:
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and (execution_id := self.deliveries.get(part.tool_call_id)):
                    await runtime.acknowledge_delivery(thread_id, execution_id)
                    self.deliveries.pop(part.tool_call_id)
