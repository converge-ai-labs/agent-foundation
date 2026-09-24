"""Public, secret-minimizing models for compatible Model account stores."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Provider(StrEnum):
    CODEX = "codex"
    GROK = "grok"
    COPILOT = "copilot"


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
    account_id: str | None = None
    source_id: str | None = None
    shared_with_cli: bool = False
    message: str | None = None


class AccountSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["native", "copilot_cli_file"]
    account_id: str = Field(min_length=1, max_length=100)


@dataclass(frozen=True, slots=True)
class AccountCandidate:
    selection: AccountSelection
    label: str
    selected: bool


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
    "AccountCandidate",
    "AccountProjection",
    "AccountSelection",
    "AccountStoreConflictError",
    "AccountStoreError",
    "Availability",
    "ExpiryStatus",
    "Provider",
    "RequiredAction",
    "StoreKind",
    "StorePolicy",
]
