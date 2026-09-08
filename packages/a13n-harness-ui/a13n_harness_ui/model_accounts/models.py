"""Public, secret-minimizing models for compatible Model account stores."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any


class Provider(StrEnum):
    CODEX = "codex"
    GROK = "grok"


class StoreKind(StrEnum):
    FILE = "file"
    KEYRING = "keyring"
    AUTO = "auto"
    EPHEMERAL = "ephemeral"
    PROCESS = "process"


class Availability(StrEnum):
    AVAILABLE = "available"
    ABSENT = "absent"
    INCOMPATIBLE = "incompatible"
    UNSUPPORTED = "unsupported"


class ExpiryStatus(StrEnum):
    VALID = "valid"
    EXPIRING = "expiring"
    EXPIRED = "expired"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class RequiredAction(StrEnum):
    NONE = "none"
    LOGIN = "login"
    REFRESH = "refresh"
    REAUTHENTICATE = "reauthenticate"
    SWITCH_TO_FILE = "switch_to_file"


@dataclass(frozen=True, slots=True)
class StorePolicy:
    """One resolved upstream credential source and write target."""

    provider: Provider
    kind: StoreKind
    path: Path | None
    supported: bool
    writable: bool
    required_action: RequiredAction = RequiredAction.NONE


@dataclass(frozen=True, slots=True)
class AccountProjection:
    """Bounded diagnostics safe for logs, APIs, and UI projection."""

    provider: Provider
    availability: Availability
    source: StoreKind
    usable: bool
    expiry: ExpiryStatus
    expires_at: datetime | None = None
    required_action: RequiredAction = RequiredAction.NONE


class AccountStoreError(Exception):
    """A bounded compatible-store failure with no credential-bearing payload."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        provider: Provider,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.provider = provider
        self.details = MappingProxyType(dict(details or {}))


class AccountStoreConflictError(AccountStoreError):
    """The active shared account changed during a mutation."""


__all__ = [
    "AccountProjection",
    "AccountStoreConflictError",
    "AccountStoreError",
    "Availability",
    "ExpiryStatus",
    "Provider",
    "RequiredAction",
    "StoreKind",
    "StorePolicy",
]
