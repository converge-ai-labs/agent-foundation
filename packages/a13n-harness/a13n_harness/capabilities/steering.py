"""Native active-run steering and compact-replay input retention."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import EnqueuedMessagesEvent, ModelRequest, UserPromptPart

from a13n_harness.content import ContentItem, ContentMetadata, input_request
from a13n_harness.errors import DefinitionError, RunError
from a13n_harness.events import HarnessEventEmitter, SteeringInputEnqueuedPayload, emit_harness_event
from a13n_harness.input import RunInputValue, SemanticRunInput, normalize_input
from a13n_harness.state import AgentContextState

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from pydantic_ai.capabilities import WrapModelRequestHandler
    from pydantic_ai.messages import ModelMessage, ModelResponse
    from pydantic_ai.models import ModelRequestContext

    from a13n_harness.context import AgentContext

STEERING_CAPABILITY_ID = "a13n.steering"
_STEERING_STATE_VERSION = "1"
_SOURCE_RUN_METADATA_KEY = "a13n.steering-run"
_INPUT_ID_METADATA_KEY = "a13n.steering-input"
_NOTIFICATION_SOURCE_METADATA_KEY = "a13n.steering-source"
_NOTIFICATION_ID_METADATA_KEY = "a13n.notification-input"
_NOTIFICATION_REFERENCES_METADATA_KEY = "a13n.notification-references"

type SteeringInputSource = Literal["external", "async_subagent", "background_process"]


class _SteeringState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    retained_requests: tuple[ModelRequest, ...] = ()
    pending_requests: tuple[ModelRequest, ...] = ()

    @field_validator("retained_requests", "pending_requests")
    @classmethod
    def _validate_retained_requests(cls, value: tuple[ModelRequest, ...]) -> tuple[ModelRequest, ...]:
        for request in value:
            if len(request.parts) != 1 or not isinstance(request.parts[0], UserPromptPart):
                raise ValueError("retained steering state must contain user-only requests")
        return value


class SteeringBridge:
    """One run-local bridge from the public stream to native Pydantic enqueue."""

    def __init__(
        self,
        state: AgentContextState,
        *,
        run_id: str,
        retain_inputs: bool,
        events: HarnessEventEmitter,
        delivered: Callable[[EnqueuedMessagesEvent], None] | None = None,
    ) -> None:
        self._state = state
        self._run_id = run_id
        self._retain_inputs = retain_inputs
        self._events = events
        self._delivered = delivered
        self._retained_requests: tuple[ModelRequest, ...] = ()
        self._pending_requests: dict[str, ModelRequest] = {}
        self._active_context: RunContext[Any] | None = None
        self._prepared = False
        self._between_attempts = False
        self.model_active = False
        self.ready = asyncio.Event()
        self._lock = asyncio.Lock()

    @property
    def active(self) -> bool:
        return self._active_context is not None

    @property
    def retained_requests(self) -> tuple[ModelRequest, ...]:
        return deepcopy(self._retained_requests)

    def replay_requests(
        self, native_run_id: str | None, conversation_id: str | None = None
    ) -> tuple[ModelRequest, ...]:
        """Return retained inputs with current logical-run provenance restored."""
        requests = deepcopy(self._retained_requests)
        if native_run_id is None:
            return requests
        replayed = []
        for request in requests:
            if request.metadata is not None and request.metadata.get(_SOURCE_RUN_METADATA_KEY) == self._run_id:
                part = request.parts[0]
                assert isinstance(part, UserPromptPart)
                request = replace(
                    request,
                    timestamp=request.timestamp or part.timestamp,
                    run_id=native_run_id,
                    conversation_id=request.conversation_id or conversation_id,
                )
            replayed.append(request)
        return tuple(replayed)

    async def prepare(self, *, restore_retained: bool) -> tuple[ModelRequest, ...]:
        """Start the logical Run's ledger and restore its accepted pending inputs."""
        async with self._lock:
            if self._prepared:
                return ()
            restored = await self._state.read(STEERING_CAPABILITY_ID, _SteeringState, version=_STEERING_STATE_VERSION)
            pending = restored.pending_requests if restored is not None else ()
            self._pending_requests = {uuid4().hex: request for request in pending}
            retained: tuple[ModelRequest, ...] = ()
            if self._retain_inputs:
                if restore_retained and restored is not None:
                    retained = restored.retained_requests
                await self._write(retained)
            self._retained_requests = deepcopy(retained)
            self._prepared = True
            return deepcopy(pending)

    async def retain_initial(self, input: SemanticRunInput) -> None:
        """Append this Run's input after restored pending inputs enter history."""
        if self._retain_inputs and input.value is not None:
            async with self._lock:
                self._retained_requests = (
                    *self._retained_requests,
                    _request_for_input(input.value, source_run_id=self._run_id),
                )
                await self._write(self._retained_requests)

    async def replace_retained(self, prompt: str) -> None:
        """Replace delivered inputs after lossy recovery, preserving accepted pending inputs."""
        async with self._lock:
            request = _request_for_input(prompt, source_run_id=self._run_id)
            self._retained_requests = (request,) if self._retain_inputs else ()
            await self._write(self._retained_requests)

    def pause(self) -> None:
        """Keep accepting ordered input while the current native attempt drains."""
        self._between_attempts = True
        self.ready.set()

    async def checkpoint(self, messages: Sequence[ModelMessage]) -> None:
        """Reconcile incorporation and retain accepted inputs at the normal save boundary."""
        await self.resolve_delivered(messages)
        async with self._lock:
            if self._prepared:
                await self._write(self._retained_requests)

    async def steer(
        self,
        input: RunInputValue,
        *,
        input_id: str | None = None,
        before_accept: Callable[[], None] | None = None,
        after_accept: Callable[[], bool] | None = None,
    ) -> str:
        """Enqueue one user steering value for the active native run.

        A host that needs durable evidence passes its own `input_id`; `steering_input_ids` finds it in
        exported history once the value has been delivered.
        """
        if input_id is not None and not 1 <= len(input_id) <= 256:
            raise RunError("A steering input ID must have 1 to 256 characters.", code="input_invalid")
        normalized = normalize_input(input)
        assert normalized.value is not None
        request = _request_for_input(
            normalized.value,
            source_run_id=self._run_id,
            input_id=input_id or uuid4().hex,
        )
        async with self._lock:
            active = self._active_context
            if active is None and not self._between_attempts:
                raise RunError("The Agent is not accepting steering.", code="run_not_active")
            if before_accept is not None:
                before_accept()
            if self._between_attempts:
                enqueue_id = uuid4().hex
            else:
                assert active is not None
                enqueue_id = active.enqueue(request, priority="asap")
            if enqueue_id is None:
                raise RunError("Steering input must not be empty.", code="input_empty")
            self._pending_requests[enqueue_id] = deepcopy(request)
            handoff_requested = after_accept() if after_accept is not None else False
        await self._emit_enqueued(enqueue_id, source="external", references=(), handoff_requested=handoff_requested)
        return enqueue_id

    async def notify(
        self,
        message: str,
        *,
        source: Literal["async_subagent", "background_process"],
        references: Sequence[str],
    ) -> str | None:
        """Best-effort enqueue one Harness lifecycle notice when this Run is active."""
        active = self._active_context
        if active is None:
            return None
        request = input_request(
            [ContentItem(message, ContentMetadata(display=False, source_id=f"a13n.{source}"))],
            metadata={
                _SOURCE_RUN_METADATA_KEY: self._run_id,
                _NOTIFICATION_SOURCE_METADATA_KEY: source,
                _NOTIFICATION_ID_METADATA_KEY: uuid4().hex,
                _NOTIFICATION_REFERENCES_METADATA_KEY: list(references),
            },
        )
        try:
            enqueue_id = active.enqueue(request, priority="asap")
        except (RuntimeError, ValueError):
            return None
        if enqueue_id is None:
            return None
        async with self._lock:
            self._pending_requests[enqueue_id] = deepcopy(request)
        await self._emit_enqueued(enqueue_id, source=source, references=tuple(references))
        return enqueue_id

    async def discard_notifications(
        self,
        *,
        source: Literal["async_subagent", "background_process"],
        references: Sequence[str],
    ) -> None:
        """Withdraw matching lifecycle hints that have not entered native history."""
        async with self._lock:
            matching = {
                enqueue_id
                for enqueue_id, request in self._pending_requests.items()
                if (metadata := request.metadata) is not None
                and metadata.get(_NOTIFICATION_SOURCE_METADATA_KEY) == source
                and metadata.get(_NOTIFICATION_REFERENCES_METADATA_KEY) == list(references)
            }
            active = self._active_context
            if active is None:
                # Between attempts there is no live queue; remove the replay candidates.
                for enqueue_id in matching:
                    self._pending_requests.pop(enqueue_id)
            elif active.pending_messages is not None:
                # The native drain runs on this event loop. Remove individual entries without
                # replacing the shared queue or losing concurrent sync-tool appends.
                for pending in tuple(active.pending_messages):
                    if pending.enqueue_id in matching:
                        active.pending_messages.remove(pending)
                        self._pending_requests.pop(pending.enqueue_id)

    async def resolve_delivered(self, messages: Sequence[ModelMessage]) -> None:
        """Resolve pending public inputs already present in canonical history."""
        delivered_ids = set((*steering_input_ids(messages), *_input_ids(messages, _NOTIFICATION_ID_METADATA_KEY)))
        if not delivered_ids:
            return
        async with self._lock:
            applied_enqueue_ids = [
                enqueue_id
                for enqueue_id, request in self._pending_requests.items()
                if request.metadata is not None
                and (
                    request.metadata.get(_INPUT_ID_METADATA_KEY) in delivered_ids
                    or request.metadata.get(_NOTIFICATION_ID_METADATA_KEY) in delivered_ids
                )
            ]
            if not applied_enqueue_ids:
                return
            if self._retain_inputs:
                retained = (
                    *self._retained_requests,
                    *(
                        self._pending_requests[enqueue_id]
                        for enqueue_id in applied_enqueue_ids
                        if _NOTIFICATION_SOURCE_METADATA_KEY not in (self._pending_requests[enqueue_id].metadata or {})
                    ),
                )
                self._retained_requests = deepcopy(retained)
            for enqueue_id in applied_enqueue_ids:
                self._observe_delivered(enqueue_id, self._pending_requests.pop(enqueue_id))
            await self._write(self._retained_requests)

    def _observe_delivered(self, enqueue_id: str, request: ModelRequest) -> None:
        if self._delivered is not None:
            self._delivered(EnqueuedMessagesEvent(enqueue_id=enqueue_id, messages=(request,)))

    async def mark_applied(self, enqueue_id: str) -> None:
        """Resolve one public steering value after native history delivery is observed."""
        async with self._lock:
            request = self._pending_requests.get(enqueue_id)
            if request is None:
                return
            if self._retain_inputs and _NOTIFICATION_SOURCE_METADATA_KEY not in (request.metadata or {}):
                retained = (*self._retained_requests, request)
                self._retained_requests = deepcopy(retained)
            self._observe_delivered(enqueue_id, self._pending_requests.pop(enqueue_id))
            await self._write(self._retained_requests)

    async def bind(self, ctx: RunContext[Any]) -> bool:
        """Bind the outer native attempt and redeliver its unresolved public inputs."""
        async with self._lock:
            if self._active_context is not None:
                return False
            self._active_context = ctx
            self._between_attempts = False
            if not self._pending_requests:
                self.ready.set()
                return True
            pending = tuple(self._pending_requests.values())
            reenqueued: dict[str, ModelRequest] = {}
            try:
                for request in pending:
                    enqueue_id = ctx.enqueue(deepcopy(request), priority="asap")
                    if enqueue_id is None:
                        raise RunError("Retained steering input was rejected.", code="input_empty")
                    reenqueued[enqueue_id] = request
            except BaseException:
                self._active_context = None
                self.ready.clear()
                raise
            self._pending_requests = reenqueued
            self.ready.set()
            return True

    def unbind(self, ctx: RunContext[Any], *, owned: bool) -> None:
        if owned and self._active_context is ctx:
            self._active_context = None
            if not self._between_attempts:
                self.ready.clear()

    async def _emit_enqueued(
        self,
        enqueue_id: str,
        *,
        source: SteeringInputSource,
        references: tuple[str, ...],
        handoff_requested: bool = False,
    ) -> None:
        try:
            await emit_harness_event(
                self._events,
                kind="lifecycle",
                payload=SteeringInputEnqueuedPayload(
                    enqueue_id=enqueue_id,
                    source=source,
                    references=references,
                    handoff_requested=handoff_requested,
                ),
            )
        except RunError:
            # Enqueue already succeeded; event delivery cannot roll back accepted input.
            return

    async def _write(self, retained: tuple[ModelRequest, ...]) -> None:
        await self._state.write(
            STEERING_CAPABILITY_ID,
            _SteeringState(
                retained_requests=retained,
                pending_requests=tuple(
                    request
                    for request in self._pending_requests.values()
                    if _INPUT_ID_METADATA_KEY in (request.metadata or {})
                ),
            ),
            version=_STEERING_STATE_VERSION,
        )


def steering_input_ids(messages: Sequence[ModelMessage]) -> tuple[str, ...]:
    """Input IDs of public steering values delivered into `messages`, in history order."""
    return _input_ids(messages, _INPUT_ID_METADATA_KEY)


def _input_ids(messages: Sequence[ModelMessage], key: str) -> tuple[str, ...]:
    return tuple(
        input_id
        for message in messages
        if isinstance(message, ModelRequest) and message.metadata is not None
        for value in [message.metadata.get(key)]
        for input_id in (value if isinstance(value, list) else [value])
        if isinstance(input_id, str)
    )


def _request_for_input(
    input: str | tuple[Any, ...],
    *,
    source_run_id: str,
    input_id: str | None = None,
) -> ModelRequest:
    metadata = {_SOURCE_RUN_METADATA_KEY: source_run_id}
    if input_id is not None:
        metadata[_INPUT_ID_METADATA_KEY] = input_id
    from a13n_harness._context_recovery import mark_input
    from a13n_harness.content import annotate_prompt, content_items

    content = deepcopy(input)
    request = input_request(content, metadata=metadata)
    return annotate_prompt(
        request,
        0,
        mark_input(content_items(content), kind="steer" if input_id is not None else "task", run_id=source_run_id),
    )


@dataclass(init=False)
class SteeringCapability(AbstractCapability["AgentContext"]):
    """Bind the public Harness stream to one active Pydantic RunContext."""

    id = STEERING_CAPABILITY_ID

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost")

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(STEERING_CAPABILITY_ID)
        if existing is not None:
            if not isinstance(existing, SteeringCapability):
                raise DefinitionError(
                    "Steering has an incompatible run replacement.",
                    code="capability_type_mismatch",
                )
            return existing
        replacement = SteeringCapability()
        ctx.deps._record_run_capability(STEERING_CAPABILITY_ID, replacement)
        return replacement

    async def wrap_model_request(
        self,
        ctx: RunContext[AgentContext],
        *,
        request_context: ModelRequestContext,
        handler: WrapModelRequestHandler,
    ) -> ModelResponse:
        # Only the primary request owns the interruptible generation window.
        # Tool preparation and response-to-tool transitions stay outside it;
        # review/helper Agents sharing these deps cannot change its phase.
        if ctx.run_id != ctx.deps._model_input.attempt_id:
            return await handler(request_context)
        bridge = ctx.deps._steering
        ctx.deps._tool_executions.handoff_requested.clear()
        bridge.model_active = True
        try:
            return await handler(request_context)
        finally:
            # Revoke preemption synchronously before the response is committed
            # and tools can start. Native cancellation drains the request first.
            bridge.model_active = False

    async def wrap_run(
        self,
        ctx: RunContext[AgentContext],
        *,
        handler: Callable[[], Awaitable[Any]],
    ) -> Any:
        owned = await ctx.deps._steering.bind(ctx)
        try:
            return await handler()
        finally:
            ctx.deps._steering.unbind(ctx, owned=owned)


__all__ = ["STEERING_CAPABILITY_ID", "SteeringBridge", "SteeringCapability", "steering_input_ids"]
