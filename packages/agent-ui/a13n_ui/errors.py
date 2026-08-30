"""Agent UI application and local-store failures."""

from __future__ import annotations

from typing import Any


class AgentUiError(Exception):
    """Base class for explicit Agent UI failures."""

    def __init__(self, message: str, *, code: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class HostStateError(AgentUiError):
    """The Host cannot accept the requested operation in its current state."""


class ConfigurationError(AgentUiError):
    """A configuration candidate or source edit is invalid or conflicted."""


class SkillManagementError(ConfigurationError):
    """A local Skill scan, copy, validation, or import could not complete safely."""


class CompositionError(ConfigurationError):
    """An Agent or Environment snapshot could not be resolved or reconstructed."""


class StoreError(AgentUiError):
    """The local store could not complete an operation safely."""


class StoreLeaseConflict(StoreError):
    """Another process currently owns the selected Agent UI data root."""


class StoreIntegrityError(StoreError):
    """Stored authority is missing, corrupt, incompatible, or internally inconsistent."""


class SessionError(AgentUiError):
    """A Session, Thread, Turn, or checkpoint command is invalid or conflicted."""


class EnvironmentLifecycleError(AgentUiError):
    """An Environment resource lifecycle command could not complete safely."""


class RunCoordinationError(AgentUiError):
    """A foreground Harness Run could not be accepted or committed safely."""


class EventStoreError(StoreError):
    """Retained AG-UI history could not be published, verified, or replayed."""


class RuntimeResolutionError(AgentUiError):
    """A required Host-native runtime could not be resolved or verified."""


class RuntimeGenerationError(AgentUiError):
    """A runtime Runner generation could not start, promote, drain, or stop safely."""


class ObjectIntegrityError(StoreIntegrityError):
    """An immutable object failed codec, identity, or payload validation."""


__all__ = [
    "AgentUiError",
    "CompositionError",
    "ConfigurationError",
    "EnvironmentLifecycleError",
    "EventStoreError",
    "HostStateError",
    "ObjectIntegrityError",
    "RunCoordinationError",
    "RuntimeGenerationError",
    "RuntimeResolutionError",
    "SessionError",
    "SkillManagementError",
    "StoreError",
    "StoreIntegrityError",
    "StoreLeaseConflict",
]
