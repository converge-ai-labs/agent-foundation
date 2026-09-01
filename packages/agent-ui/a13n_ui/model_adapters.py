"""Agent UI-owned trusted Model adapter provenance."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

_BUILTIN_ADAPTER_DISTRIBUTION = "a13n-ui"
_PYDANTIC_AI_ADAPTER_KEY = "a13n.pydantic-ai"


@dataclass(frozen=True, slots=True)
class ModelAdapterRegistration:
    """Installed provenance for one Agent UI-owned Model adapter."""

    adapter_key: str
    distribution_name: str
    distribution_version: str


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


__all__ = ["ModelAdapterRegistration", "model_adapter_registration"]
