"""Trusted distribution inputs for built-in Plugin registration."""

from __future__ import annotations

from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass

from .domain import BuiltinPluginRegistration

BuiltinPluginBodyFactory = Callable[[], AsyncIterable[bytes]]


@dataclass(frozen=True, slots=True)
class BuiltinPluginArtifact:
    """One release-manifest entry paired with its immutable bundled Wheel."""

    registration: BuiltinPluginRegistration
    body_factory: BuiltinPluginBodyFactory
    content_length: int

    def __post_init__(self) -> None:
        if self.content_length <= 0:
            raise ValueError("built-in Plugin content length must be positive")


__all__ = ["BuiltinPluginArtifact", "BuiltinPluginBodyFactory"]
