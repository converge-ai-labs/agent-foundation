"""Agent UI application and local-store failures."""

from __future__ import annotations

from typing import Any


class AgentUiError(Exception):
    """Base class for explicit Agent UI failures."""

    def __init__(self, message: str, *, code: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class ApplicationStateError(AgentUiError):
    """The application cannot accept the requested operation in its current state."""


class ConfigurationError(AgentUiError):
    """A configuration candidate or source edit is invalid or conflicted."""


class SkillManagementError(ConfigurationError):
    """A local Skill scan, copy, validation, or import could not complete safely."""


class StoreError(AgentUiError):
    """The local store could not complete an operation safely."""


class StoreLeaseConflict(StoreError):
    """Another process currently owns the selected Agent UI data root."""


class StoreIntegrityError(StoreError):
    """Stored authority is missing, corrupt, incompatible, or internally inconsistent."""


class ObjectIntegrityError(StoreIntegrityError):
    """An immutable object failed codec, identity, or payload validation."""


__all__ = [
    "AgentUiError",
    "ApplicationStateError",
    "ConfigurationError",
    "ObjectIntegrityError",
    "SkillManagementError",
    "StoreError",
    "StoreIntegrityError",
    "StoreLeaseConflict",
]
