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
from pydantic_ai.messages import ModelRequest, TextContent, UserPromptPart

from a13n_harness.errors import DefinitionError, RunError
from a13n_harness.events import HarnessEventEmitter, SteeringInputEnqueuedPayload, emit_harness_event
from a13n_harness.input import RunInputValue, SemanticRunInput, normalize_input
from a13n_harness.model_context import ModelInputEvent, user_prompt_content
from a13n_harness.state import AgentContextState

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from pydantic_ai.messages import ModelMessage

    from a13n_harness.context import AgentContext

STEERING_CAPABILITY_ID = "a13n.steering"
_STEERING_STATE_VERSION = "1"
_SOURCE_RUN_METADATA_KEY = "a13n.steering-run"
_INPUT_ID_METADATA_KEY = "a13n.steering-input"
_NOTIFICATION_SOURCE_METADATA_KEY = "a13n.steering-source"

type SteeringInputSource = Literal["external", "async_subagent", "background_process"]


class _SteeringState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    retained_requests: tuple[ModelRequest, ...] = ()

    @field_validator("retained_requests")
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
    ) -> None:
        self._state = state
        self._run_id = run_id
        self._retain_inputs = retain_inputs
        self._events = events
        self._retained_requests: tuple[ModelRequest, ...] = ()
        self._pending_requests: dict[str, ModelRequest] = {}
        self._active_context: RunContext[Any] | None = None
        self._prepared = False
        self._lock = asyncio.Lock()

    @property
    def active(self) -> bool:
        return self._active_context is not None

    @property
    def retained_requests(self) -> tuple[ModelRequest, ...]:
        return deepcopy(self._retained_requests)

    def replay_requests(self, native_run_id: str | None) -> tuple[ModelRequest, ...]:
        """Return retained inputs with current logical-run provenance restored."""
        requests = deepcopy(self._retained_requests)
        if native_run_id is None:
            return requests
        return tuple(
            replace(request, run_id=native_run_id)
            if request.metadata is not None and request.metadata.get(_SOURCE_RUN_METADATA_KEY) == self._run_id
            else request
            for request in requests
        )

    async def prepare(self, input: SemanticRunInput, *, restore_retained: bool) -> None:
        """Start this logical run's retained-input ledger."""
        async with self._lock:
            if self._prepared:
                return
            retained: tuple[ModelRequest, ...] = ()
            if self._retain_inputs:
                if restore_retained:
                    restored = await self._state.read(
                        STEERING_CAPABILITY_ID,
                        _SteeringState,
                        version=_STEERING_STATE_VERSION,
                    )
                    if restored is not None:
                        retained = restored.retained_requests
                if input.value is not None:
                    retained = (*retained, _request_for_input(input.value, source_run_id=self._run_id))
                await self._write(retained)
            self._retained_requests = deepcopy(retained)
            self._prepared = True

    async def steer(self, input: RunInputValue, *, input_id: str | None = None) -> str:
        """Enqueue one user steering value for the active native run.

        A host that needs durable evidence passes its own `input_id`; `steering_input_ids` finds it in
        exported history once the value has been delivered.
        """
        active = self._active_context
        if active is None:
            raise RunError("The Agent is not accepting steering.", code="run_not_active")
        if input_id is not None and not 1 <= len(input_id) <= 256:
            raise RunError("A steering input ID must have 1 to 256 characters.", code="input_invalid")
        normalized = normalize_input(input)
        assert normalized.value is not None
        request = _request_for_input(
            normalized.value,
            source_run_id=self._run_id,
            input_id=input_id or uuid4().hex,
        )
        enqueue_id = active.enqueue(request, priority="asap")
        if enqueue_id is None:
            raise RunError("Steering input must not be empty.", code="input_empty")
        async with self._lock:
            self._pending_requests[enqueue_id] = deepcopy(request)
        await self._emit_enqueued(enqueue_id, source="external", references=())
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
        request = ModelRequest(
            parts=[
                UserPromptPart(content=[TextContent(message, metadata={_NOTIFICATION_SOURCE_METADATA_KEY: source})])
            ],
            metadata={
                _SOURCE_RUN_METADATA_KEY: self._run_id,
                _NOTIFICATION_SOURCE_METADATA_KEY: source,
            },
        )
        try:
            enqueue_id = active.enqueue(request, priority="asap")
        except (RuntimeError, ValueError):
            return None
        if enqueue_id is None:
            return None
        await self._emit_enqueued(enqueue_id, source=source, references=tuple(references))
        return enqueue_id

    async def resolve_delivered(self, messages: Sequence[ModelMessage]) -> None:
        """Resolve pending public inputs already present in canonical history."""
        delivered_ids = set(steering_input_ids(messages))
        if not delivered_ids:
            return
        async with self._lock:
            applied_enqueue_ids = [
                enqueue_id
                for enqueue_id, request in self._pending_requests.items()
                if request.metadata is not None and request.metadata.get(_INPUT_ID_METADATA_KEY) in delivered_ids
            ]
            if not applied_enqueue_ids:
                return
            if self._retain_inputs:
                retained = (
                    *self._retained_requests,
                    *(self._pending_requests[enqueue_id] for enqueue_id in applied_enqueue_ids),
                )
                await self._write(retained)
                self._retained_requests = deepcopy(retained)
            for enqueue_id in applied_enqueue_ids:
                self._pending_requests.pop(enqueue_id)

    async def mark_applied(self, enqueue_id: str) -> None:
        """Resolve one public steering value after native history delivery is observed."""
        async with self._lock:
            request = self._pending_requests.get(enqueue_id)
            if request is None:
                return
            if self._retain_inputs:
                retained = (*self._retained_requests, request)
                await self._write(retained)
                self._retained_requests = deepcopy(retained)
            self._pending_requests.pop(enqueue_id)

    async def bind(self, ctx: RunContext[Any]) -> bool:
        """Bind the outer native attempt and redeliver its unresolved public inputs."""
        async with self._lock:
            if self._active_context is not None:
                return False
            self._active_context = ctx
            if not self._pending_requests:
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
                raise
            self._pending_requests = reenqueued
            return True

    def unbind(self, ctx: RunContext[Any], *, owned: bool) -> None:
        if owned and self._active_context is ctx:
            self._active_context = None

    async def _emit_enqueued(
        self,
        enqueue_id: str,
        *,
        source: SteeringInputSource,
        references: tuple[str, ...],
    ) -> None:
        try:
            await emit_harness_event(
                self._events,
                kind="lifecycle",
                payload=SteeringInputEnqueuedPayload(
                    enqueue_id=enqueue_id,
                    source=source,
                    references=references,
                ),
            )
        except RunError:
            # Enqueue already succeeded; event delivery cannot roll back accepted input.
            return

    async def _write(self, retained: tuple[ModelRequest, ...]) -> None:
        await self._state.write(
            STEERING_CAPABILITY_ID,
            _SteeringState(retained_requests=retained),
            version=_STEERING_STATE_VERSION,
        )


def steering_input_ids(messages: Sequence[ModelMessage]) -> tuple[str, ...]:
    """Input IDs of the steering values delivered into `messages`, in history order."""
    return tuple(
        input_id
        for message in messages
        if isinstance(message, ModelRequest) and message.metadata is not None
        if isinstance(input_id := message.metadata.get(_INPUT_ID_METADATA_KEY), str)
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
    return ModelRequest(
        parts=[UserPromptPart(content=deepcopy(input))],
        metadata=metadata,
    )


@dataclass(init=False)
class SteeringCapability(AbstractCapability["AgentContext"]):
    """Bind the public Harness stream to one active Pydantic RunContext."""

    id = STEERING_CAPABILITY_ID
    _input_observed: bool = False

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

    async def wrap_run(
        self,
        ctx: RunContext[AgentContext],
        *,
        handler: Callable[[], Awaitable[Any]],
    ) -> Any:
        owned = await ctx.deps._steering.bind(ctx)
        try:
            if owned and not self._input_observed:
                self._input_observed = True
                if ctx.prompt is not None:
                    await ctx.emit(ModelInputEvent(content=user_prompt_content(UserPromptPart(ctx.prompt))))
            return await handler()
        finally:
            ctx.deps._steering.unbind(ctx, owned=owned)


__all__ = ["STEERING_CAPABILITY_ID", "SteeringBridge", "SteeringCapability", "steering_input_ids"]
