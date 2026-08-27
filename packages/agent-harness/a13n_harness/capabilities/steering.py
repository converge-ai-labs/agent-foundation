"""Native active-run steering and compact-replay input retention."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import ModelRequest, UserPromptPart

from a13n_harness.errors import DefinitionError, RunError
from a13n_harness.input import RunInputValue, SemanticRunInput, normalize_input
from a13n_harness.state import AgentContextState

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from a13n_harness.context import AgentContext

STEERING_CAPABILITY_ID = "a13n.steering"
_STEERING_STATE_VERSION = "1"
_SOURCE_RUN_METADATA_KEY = "a13n.steering-run"


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

    def __init__(self, state: AgentContextState, *, run_id: str, retain_inputs: bool) -> None:
        self._state = state
        self._run_id = run_id
        self._retain_inputs = retain_inputs
        self._retained_requests: tuple[ModelRequest, ...] = ()
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

    async def prepare(self, input: SemanticRunInput) -> None:
        """Load retained state once and append this logical run's semantic input."""
        async with self._lock:
            if self._prepared:
                return
            retained = ()
            if self._retain_inputs:
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

    async def steer(self, input: RunInputValue) -> str:
        """Retain one user steering value and enqueue it on the active native run."""
        active = self._active_context
        if active is None:
            raise RunError("The Agent is not accepting steering.", code="run_not_active")
        normalized = normalize_input(input)
        assert normalized.value is not None
        if self._retain_inputs:
            async with self._lock:
                retained = (
                    *self._retained_requests,
                    _request_for_input(normalized.value, source_run_id=self._run_id),
                )
                await self._write(retained)
                self._retained_requests = deepcopy(retained)
        enqueue_id = (
            active.enqueue(normalized.value, priority="asap")
            if isinstance(normalized.value, str)
            else active.enqueue(*normalized.value, priority="asap")
        )
        if enqueue_id is None:
            raise RunError("Pydantic AI rejected an empty steering value.", code="input_empty")
        return enqueue_id

    def bind(self, ctx: RunContext[Any]) -> bool:
        """Bind only the outer active run; nested compact runs leave it unchanged."""
        if self._active_context is not None:
            return False
        self._active_context = ctx
        return True

    def unbind(self, ctx: RunContext[Any], *, owned: bool) -> None:
        if owned and self._active_context is ctx:
            self._active_context = None

    async def _write(self, retained: tuple[ModelRequest, ...]) -> None:
        await self._state.write(
            STEERING_CAPABILITY_ID,
            _SteeringState(retained_requests=retained),
            version=_STEERING_STATE_VERSION,
        )


def _request_for_input(input: str | tuple[Any, ...], *, source_run_id: str) -> ModelRequest:
    return ModelRequest(
        parts=[UserPromptPart(content=deepcopy(input))],
        metadata={_SOURCE_RUN_METADATA_KEY: source_run_id},
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

    async def wrap_run(
        self,
        ctx: RunContext[AgentContext],
        *,
        handler: Callable[[], Awaitable[Any]],
    ) -> Any:
        owned = ctx.deps._steering.bind(ctx)
        try:
            return await handler()
        finally:
            ctx.deps._steering.unbind(ctx, owned=owned)


__all__ = ["STEERING_CAPABILITY_ID", "SteeringBridge", "SteeringCapability"]
