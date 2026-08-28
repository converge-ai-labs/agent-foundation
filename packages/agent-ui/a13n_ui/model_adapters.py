"""Agent UI-owned trusted Model adapter provenance."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

from a13n_harness import RunModelResolver

from a13n_ui.errors import RunCoordinationError

if TYPE_CHECKING:
    from a13n_ui.composition import ResolvedAgentSnapshot

_BUILTIN_ADAPTER_DISTRIBUTION = "a13n-ui"
_PYDANTIC_AI_ADAPTER_KEY = "a13n.pydantic-ai"

type RunModelResolverFactory = Callable[["ResolvedAgentSnapshot"], Awaitable[RunModelResolver]]


@dataclass(frozen=True, slots=True)
class ModelAdapterRegistration:
    """Installed provenance for one Agent UI-owned Model adapter."""

    adapter_key: str
    distribution_name: str
    distribution_version: str


async def unavailable_run_model_resolver_factory(
    _snapshot: ResolvedAgentSnapshot,
) -> RunModelResolver:
    """Fail explicitly until the embedding Host supplies current credential resolution."""

    raise RunCoordinationError(
        "Foreground execution requires a Host-owned fresh Model resolver factory.",
        code="model_resolver_unavailable",
    )


def model_adapter_registration(adapter_key: str) -> ModelAdapterRegistration | None:
    """Return the exact built-in registration for one supported adapter key."""

    if adapter_key != _PYDANTIC_AI_ADAPTER_KEY:
        return None
    try:
        distribution_version = version(_BUILTIN_ADAPTER_DISTRIBUTION)
    except PackageNotFoundError:
        return None
    return ModelAdapterRegistration(
        adapter_key=adapter_key,
        distribution_name=_BUILTIN_ADAPTER_DISTRIBUTION,
        distribution_version=distribution_version,
    )


__all__ = [
    "ModelAdapterRegistration",
    "RunModelResolverFactory",
    "model_adapter_registration",
    "unavailable_run_model_resolver_factory",
]
