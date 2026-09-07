"""Harness UI application and local-store failures."""

from __future__ import annotations

from typing import Any


class HarnessUiError(Exception):
    """Base class for explicit Harness UI failures."""

    def __init__(self, message: str, *, code: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class AppStateError(HarnessUiError):
    """The App cannot accept the requested operation in its current state."""


class ConfigurationError(HarnessUiError):
    """A configuration candidate or source edit is invalid or conflicted."""


class SkillManagementError(ConfigurationError):
    """A local Skill scan, copy, validation, or import could not complete safely."""


class ContentPluginError(ConfigurationError):
    """A declarative Content Plugin operation could not complete safely."""


class CompositionError(ConfigurationError):
    """An Agent or Environment snapshot could not be resolved or reconstructed."""


class StoreError(HarnessUiError):
    """The local store could not complete an operation safely."""


class StoreIntegrityError(StoreError):
    """Stored authority is missing, corrupt, incompatible, or internally inconsistent."""


class StoreConflictError(StoreError):
    """A compare-and-select write observed a different current durable head."""


class ThreadError(HarnessUiError):
    """A Thread or continuation operation is invalid."""


class EnvironmentLifecycleError(HarnessUiError):
    """An Environment resource lifecycle command could not complete safely."""


class RunCoordinationError(HarnessUiError):
    """A foreground Harness Run could not execute or save its result."""


class LivePresentationError(HarnessUiError):
    """A process-local live presentation value is invalid."""


class RuntimeResolutionError(HarnessUiError):
    """A required Host-native runtime could not be resolved or verified."""


class ObjectIntegrityError(StoreIntegrityError):
    """An immutable object failed codec, identity, or payload validation."""


__all__ = [
    "AppStateError",
    "CompositionError",
    "ConfigurationError",
    "ContentPluginError",
    "EnvironmentLifecycleError",
    "HarnessUiError",
    "LivePresentationError",
    "ObjectIntegrityError",
    "RunCoordinationError",
    "RuntimeResolutionError",
    "SkillManagementError",
    "StoreConflictError",
    "StoreError",
    "StoreIntegrityError",
    "ThreadError",
]
