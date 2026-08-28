"""Host-attached monitoring for Environment-owned background processes."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable, Sequence
from copy import copy, deepcopy
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.toolsets import AbstractToolset, DynamicToolset

from a13n_harness.capabilities.context import _requires_exact_boundary
from a13n_harness.context import AgentContext
from a13n_harness.environment.commands import (
    BoundProcessHandle,
    ProcessInfo,
)
from a13n_harness.environment.dynamic import (
    DynamicEnvironmentCapability,
    _resolve_dynamic_environment_process_projector,
)
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.errors import DefinitionError
from a13n_harness.toolsets.process_monitor import (
    MonitoredProcessToolset,
)
from a13n_harness.toolsets.shell import ShellProcessProjector

MONITORED_PROCESS_CAPABILITY_ID = "a13n.monitored-process"
MONITORED_PROCESS_RUN_CAPABILITY_ID = "a13n.monitored-process.run"
_TERMINAL_PHASES = frozenset({"exited", "signaled", "timed_out", "cancelled", "failed"})


class MonitoredProcessNotification(BaseModel):
    """One bounded readiness record whose delivery is acknowledged by exact value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    notification_id: str = Field(min_length=1, max_length=256)
    kind: Literal["output", "completion", "gap"]
    process: str | None = Field(default=None, pattern=r"^process-[1-9][0-9]*$")
    phase: str | None = Field(default=None, max_length=64)
    produced_bytes: int = Field(default=0, ge=0)
    message: str | None = Field(default=None, max_length=2_000)


@runtime_checkable
class MonitoredProcessMonitor(Protocol):
    """Fresh Host collaborator retaining readiness until accepted delivery."""

    async def register(
        self,
        *,
        process: BoundProcessHandle,
        reference: str,
        environment: BoundEnvironment,
    ) -> None: ...

    async def pending(self) -> Sequence[MonitoredProcessNotification]: ...

    async def acknowledge(self, notification: MonitoredProcessNotification) -> None: ...

    async def close(self) -> None: ...


@dataclass(slots=True)
class _MonitoredProcessRecord:
    process: BoundProcessHandle
    reference: str
    environment: BoundEnvironment
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    final_notification_id: str | None = None


class InProcessMonitoredProcessMonitor:
    """Reference embedded Host monitor; the caller still creates one instance per run."""

    def __init__(
        self,
        *,
        poll_interval_seconds: float = 0.25,
        max_pending: int = 128,
        max_monitored: int = 1_024,
        on_ready: Callable[[MonitoredProcessNotification], None] | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if max_pending <= 0:
            raise ValueError("max_pending must be positive")
        if max_monitored <= 0:
            raise ValueError("max_monitored must be positive")
        self._poll_interval = poll_interval_seconds
        self._max_pending = max_pending
        self._max_monitored = max_monitored
        self._on_ready = on_ready
        self._records: dict[str, _MonitoredProcessRecord] = {}
        self._pending: OrderedDict[str, MonitoredProcessNotification] = OrderedDict()
        self._sequence = 1
        self._closed = False
        self._lock = asyncio.Lock()

    async def register(
        self,
        *,
        process: BoundProcessHandle,
        reference: str,
        environment: BoundEnvironment,
    ) -> None:
        record = _MonitoredProcessRecord(process=process, reference=reference, environment=environment)
        async with self._lock:
            if self._closed:
                raise RuntimeError("monitored process collaborator is closed")
            if reference in self._records:
                raise RuntimeError(f"process {reference!r} is already monitored")
            if len(self._records) >= min(self._max_monitored, self._max_pending):
                raise RuntimeError("monitored process collaborator cannot retain another unacknowledged completion")
            # Reserve before the first inspect so duplicate registration and close are atomic.
            self._records[reference] = record

        try:
            info = await environment.processes.inspect(process)
            if info.status.phase in _TERMINAL_PHASES:
                await self._publish(reference, info=info, kind="completion")
                return
            task = asyncio.create_task(
                self._observe(record, info),
                name=f"a13n-process-monitor-{reference}",
            )
            async with self._lock:
                if self._closed:
                    task.cancel()
                else:
                    record.task = task
            if task.cancelled():
                await asyncio.gather(task, return_exceptions=True)
                raise RuntimeError("monitored process collaborator closed during registration")
        except BaseException:
            async with self._lock:
                if not self._closed:
                    self._records.pop(reference, None)
            raise
        finally:
            record.ready.set()

    async def pending(self) -> tuple[MonitoredProcessNotification, ...]:
        async with self._lock:
            return tuple(self._pending.values())

    async def acknowledge(self, notification: MonitoredProcessNotification) -> None:
        async with self._lock:
            current = self._pending.get(notification.notification_id)
            if isinstance(current, MonitoredProcessNotification) and current == notification:
                self._pending.pop(notification.notification_id, None)
                if current.process is not None:
                    record = self._records.get(current.process)
                    if record is not None and record.final_notification_id == notification.notification_id:
                        self._records.pop(current.process, None)

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            records = tuple(self._records.values())

        if records:
            await asyncio.gather(*(record.ready.wait() for record in records))
        tasks = tuple(record.task for record in records if record.task is not None)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Final inspection occurs while the owning Environment is still alive.
        for record in records:
            try:
                info = await record.environment.processes.inspect(record.process)
            except Exception as exc:
                await self._publish_gap(record.reference, exc, allow_closed=True)
                continue
            if info.status.phase in _TERMINAL_PHASES:
                await self._publish(record.reference, info=info, kind="completion", allow_closed=True)
            else:
                await self._publish(
                    record.reference,
                    info=info,
                    kind="gap",
                    message="Monitoring ended before a terminal process state was observed.",
                    allow_closed=True,
                )

        async with self._lock:
            detached = [
                notification.model_copy(
                    update={
                        "process": None,
                        "message": _detached_notification_message(notification),
                    }
                )
                for notification in self._pending.values()
            ]
            self._pending = OrderedDict((item.notification_id, item) for item in detached)
            self._records.clear()
            self._on_ready = None

    async def _observe(self, record: _MonitoredProcessRecord, initial: ProcessInfo) -> None:
        try:
            last_produced = _produced_bytes(initial)
            output_announced = last_produced > 0
            if output_announced:
                await self._publish(record.reference, info=initial, kind="output")
            while True:
                await asyncio.sleep(self._poll_interval)
                info = await record.environment.processes.inspect(record.process)
                produced = _produced_bytes(info)
                if info.status.phase in _TERMINAL_PHASES:
                    await self._publish(record.reference, info=info, kind="completion")
                    return
                if produced > last_produced or (produced > 0 and not output_announced):
                    output_announced = True
                    last_produced = produced
                    await self._publish(record.reference, info=info, kind="output")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._publish_gap(record.reference, exc)

    async def _publish_gap(
        self,
        reference: str,
        error: Exception,
        *,
        allow_closed: bool = False,
    ) -> None:
        code = error.code if isinstance(error, EnvironmentError) else type(error).__name__
        await self._publish(
            reference,
            info=None,
            kind="gap",
            message=f"Process observation failed ({code}).",
            allow_closed=allow_closed,
        )

    async def _publish(
        self,
        reference: str,
        *,
        info: ProcessInfo | None,
        kind: Literal["output", "completion", "gap"],
        message: str | None = None,
        allow_closed: bool = False,
    ) -> None:
        callback: Callable[[MonitoredProcessNotification], None] | None
        async with self._lock:
            if self._closed and not allow_closed:
                return
            for notification_id, current in tuple(self._pending.items()):
                if current.process == reference:
                    self._pending.pop(notification_id, None)
            notification = MonitoredProcessNotification(
                notification_id=f"process-notification-{self._sequence}",
                kind=kind,
                process=reference,
                phase=info.status.phase if info is not None else None,
                produced_bytes=_produced_bytes(info) if info is not None else 0,
                message=message,
            )
            self._sequence += 1
            self._pending[notification.notification_id] = notification
            if kind in {"completion", "gap"}:
                record = self._records.get(reference)
                if record is not None:
                    record.final_notification_id = notification.notification_id
            if len(self._pending) > self._max_pending:
                raise AssertionError("monitored process retention capacity invariant violated")
            callback = self._on_ready
        if callback is not None:
            try:
                callback(notification)
            except Exception:
                # Wake-up is best effort; pending state is already authoritative.
                pass


@dataclass(kw_only=True)
class MonitoredProcessRunCapability(AbstractCapability[AgentContext]):
    """Fresh run attachment carrying the current Host monitor."""

    id: str | None = MONITORED_PROCESS_RUN_CAPABILITY_ID
    monitor: MonitoredProcessMonitor = field()

    def __post_init__(self) -> None:
        if self.id != MONITORED_PROCESS_RUN_CAPABILITY_ID:
            raise ValueError(f"MonitoredProcessRunCapability.id must be {MONITORED_PROCESS_RUN_CAPABILITY_ID!r}")
        if not isinstance(self.monitor, MonitoredProcessMonitor):
            raise TypeError("monitor must implement MonitoredProcessMonitor")


class MonitoredProcessConfiguration(BaseModel):
    """Finite model-facing command defaults for monitored process start."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_inline_bytes: int = Field(default=64 * 1024, gt=0, le=256 * 1024)
    max_output_bytes: int = Field(default=1024 * 1024, gt=0, le=4 * 1024 * 1024)
    max_notifications_per_request: int = Field(default=16, gt=0, le=128)

    def model_post_init(self, __context: Any) -> None:
        del __context
        if self.max_inline_bytes > self.max_output_bytes:
            raise ValueError("max_inline_bytes cannot exceed max_output_bytes")


@dataclass(init=False)
class MonitoredProcessCapability(AbstractCapability[AgentContext]):
    """Start monitored Environment work and inject only Host-accepted readiness records."""

    id = MONITORED_PROCESS_CAPABILITY_ID

    def __init__(self, configuration: MonitoredProcessConfiguration | None = None) -> None:
        self.configuration = (configuration or MonitoredProcessConfiguration()).model_copy(deep=True)

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(requires=(DynamicEnvironmentCapability,))

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(MONITORED_PROCESS_CAPABILITY_ID)
        if existing is not None:
            if not isinstance(existing, _MonitoredProcessActiveCapability):
                raise DefinitionError(
                    "Monitored process has an incompatible run replacement.",
                    code="capability_type_mismatch",
                )
            return existing
        replacement = _MonitoredProcessActiveCapability(
            self.configuration,
            environment=ctx.deps.environment,
            run_id=ctx.deps.run_id,
        )
        ctx.deps._record_run_capability(MONITORED_PROCESS_CAPABILITY_ID, replacement)
        return replacement


@dataclass(init=False)
class _MonitoredProcessActiveCapability(MonitoredProcessCapability):
    def __init__(
        self,
        configuration: MonitoredProcessConfiguration,
        *,
        environment: BoundEnvironment,
        run_id: str,
    ) -> None:
        super().__init__(configuration)
        self._monitor: MonitoredProcessMonitor | None = None
        self._projector: ShellProcessProjector | None = None
        self._environment = environment
        self._run_id = run_id
        self._cleanup_registered = False

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        if ctx.deps.run_id != self._run_id:
            raise DefinitionError(
                "Monitored process run replacement cannot cross logical runs.",
                code="capability_scope_invalid",
            )
        return self

    def get_toolset(self) -> AbstractToolset[AgentContext]:
        return DynamicToolset(self._toolset_for_run, per_run_step=False, id="a13n-monitored-process")

    async def _toolset_for_run(self, ctx: RunContext[AgentContext]) -> AbstractToolset[AgentContext]:
        monitor, projector = self._bind(ctx)
        return MonitoredProcessToolset(
            monitor=monitor,
            projector=projector,
            environment=self._environment,
            configuration=self.configuration,
        ).get_toolset()

    async def wrap_model_request(
        self,
        ctx: RunContext[AgentContext],
        *,
        request_context: ModelRequestContext,
        handler: Any,
    ) -> ModelResponse:
        monitor, _ = self._bind(ctx)
        if _requires_exact_boundary(ctx, request_context.messages):
            return await handler(request_context)
        pending = tuple(await monitor.pending())[: self.configuration.max_notifications_per_request]
        if not pending or not request_context.messages or not isinstance(request_context.messages[-1], ModelRequest):
            return await handler(request_context)
        messages = deepcopy(request_context.messages)
        final = messages[-1]
        assert isinstance(final, ModelRequest)
        final = replace(final, parts=(*final.parts, UserPromptPart(_render_notifications(pending))))
        messages[-1] = final
        updated = copy(request_context)
        updated.messages = messages
        response = await handler(updated)
        for notification in pending:
            await monitor.acknowledge(notification)
        return response

    def _bind(
        self,
        ctx: RunContext[AgentContext],
    ) -> tuple[MonitoredProcessMonitor, ShellProcessProjector]:
        provenance = ctx.deps._capability_provenance
        owner = ctx.capabilities.get(MONITORED_PROCESS_CAPABILITY_ID)
        if type(owner) is not _MonitoredProcessActiveCapability or owner is not self:
            raise DefinitionError(
                "The finalized monitored-process owner has an incompatible identity.",
                code="capability_scope_invalid",
            )
        if MONITORED_PROCESS_CAPABILITY_ID not in provenance.definition_ids:
            raise DefinitionError(
                "MonitoredProcessCapability must originate from the Agent definition.",
                code="capability_scope_invalid",
            )
        attachment = ctx.capabilities.get(MONITORED_PROCESS_RUN_CAPABILITY_ID)
        if type(attachment) is not MonitoredProcessRunCapability:
            raise DefinitionError(
                "MonitoredProcessCapability requires one fresh MonitoredProcessRunCapability.",
                code="monitored_process_binding_missing",
            )
        if MONITORED_PROCESS_RUN_CAPABILITY_ID not in provenance.run_ids:
            raise DefinitionError(
                "MonitoredProcessRunCapability must originate from RunBindings.",
                code="capability_scope_invalid",
            )
        if self._monitor is not None and self._projector is not None:
            return self._monitor, self._projector
        projector = _resolve_dynamic_environment_process_projector(ctx)
        if projector is None:
            raise DefinitionError(
                "MonitoredProcessCapability requires DynamicEnvironmentCapability and its sole process reference domain.",
                code="monitored_process_dynamic_environment_missing",
            )
        self._monitor = attachment.monitor
        self._projector = projector
        if not self._cleanup_registered:
            ctx.deps._register_run_cleanup(MONITORED_PROCESS_CAPABILITY_ID, attachment.monitor.close)
            self._cleanup_registered = True
        return attachment.monitor, projector


def _produced_bytes(info: ProcessInfo) -> int:
    return info.output.stdout.produced_bytes + info.output.stderr.produced_bytes


def _detached_notification_message(notification: MonitoredProcessNotification) -> str:
    prefix = notification.message.strip() if notification.message else ""
    suffix = "This observation belongs to a previous logical run."
    return f"{prefix} {suffix}".strip()


def _render_notifications(notifications: tuple[MonitoredProcessNotification, ...]) -> str:
    lines = ["<monitored-process-notifications>"]
    for notification in notifications:
        reference = notification.process or "prior-host-process"
        if notification.kind == "completion":
            detail = f"{reference} completed with phase {notification.phase or 'unknown'}."
        elif notification.kind == "gap":
            detail = f"{reference} has an observation gap at phase {notification.phase or 'unknown'}."
        else:
            detail = f"{reference} has new buffered output ({notification.produced_bytes} bytes observed)."
        if notification.message:
            detail = f"{detail} {notification.message}"
        lines.append(f"  <notification id={notification.notification_id!r}>{detail}</notification>")
    lines.append("</monitored-process-notifications>")
    return "\n".join(lines)


__all__ = [
    "InProcessMonitoredProcessMonitor",
    "MonitoredProcessCapability",
    "MonitoredProcessConfiguration",
    "MonitoredProcessMonitor",
    "MonitoredProcessNotification",
    "MonitoredProcessRunCapability",
]
