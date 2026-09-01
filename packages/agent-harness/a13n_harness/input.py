"""Code-first semantic input boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from pydantic import JsonValue
from pydantic_ai.messages import UserContent

from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.errors import InputError
from a13n_harness.identity import AgentInstanceContext

NativeRunInput = str | Sequence[UserContent]
RunInputValue = NativeRunInput


@dataclass(frozen=True, slots=True)
class RunPreparationContext:
    """Restricted context used to produce semantic input before Agent start."""

    run_id: str
    instance: AgentInstanceContext
    environment: BoundEnvironment
    metadata: Mapping[str, JsonValue]


RunInputFactory = Callable[[RunPreparationContext], Awaitable[RunInputValue]]


@dataclass(frozen=True, slots=True)
class SemanticRunInput:
    """Normalized process-local input seen by Harness middleware."""

    value: str | tuple[UserContent, ...] | None

    def replace(self, value: RunInputValue | None) -> SemanticRunInput:
        """Return a new semantic input after applying normal validation."""
        return replace(self, value=normalize_input(value).value)


def normalize_input(value: RunInputValue | None) -> SemanticRunInput:
    """Normalize native Pydantic AI input without inventing an empty prompt."""
    if value is None:
        return SemanticRunInput(value=None)
    if isinstance(value, str):
        if not value:
            raise InputError("Run input must not be empty.", code="input_empty")
        return SemanticRunInput(value=value)
    if isinstance(value, (bytes, bytearray)) or not isinstance(value, Sequence):
        raise InputError(
            "Run input must be a string or a sequence of Pydantic AI UserContent values.",
            code="input_invalid",
        )
    normalized = tuple(value)
    if not normalized:
        raise InputError("Run input must not be empty.", code="input_empty")
    return SemanticRunInput(value=normalized)
