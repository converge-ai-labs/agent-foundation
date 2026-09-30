"""Code-first semantic input boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from pydantic import JsonValue
from pydantic_ai.messages import UserContent

from a13n_harness.content import ContentItem
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.errors import InputError
from a13n_harness.identity import AgentInstanceContext

NativeRunInput = str | Sequence[UserContent]
RunInputValue = str | Sequence[UserContent | ContentItem]


@dataclass(slots=True)
class ModelInputState:
    """Run-local input state reset at each primary attempt boundary."""

    attempt_id: str | None = None
    content: tuple[ContentItem, ...] | None = None
    source: Literal["user", "recovery"] = "user"
    annotated: bool = False
    observed: bool = False

    def begin(self, attempt_id: str, content: tuple[ContentItem, ...] | None, *, recovery: bool) -> None:
        self.attempt_id = attempt_id
        self.content = (
            tuple(ContentItem(item.value, item.metadata.model_copy(update={"display": False})) for item in content)
            if recovery and content is not None
            else content
        )
        self.source = "recovery" if recovery else "user"
        self.annotated = False
        self.observed = False


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

    value: str | tuple[UserContent | ContentItem, ...] | None

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
            "Run input must be a string or a sequence of user content values.",
            code="input_invalid",
        )
    normalized = tuple(value)
    if not normalized:
        raise InputError("Run input must not be empty.", code="input_empty")
    return SemanticRunInput(value=normalized)
