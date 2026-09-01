from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from ._json import JsonBoundaryError, detach_json_object

_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


class EnvironmentProviderErrorCategory(StrEnum):
    INVALID = "invalid"
    UNSUPPORTED = "unsupported"
    MISSING = "missing"
    DENIED = "denied"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    UNKNOWN_OUTCOME = "unknown_outcome"
    CLEANUP = "cleanup"
    PROVIDER_FAILURE = "provider_failure"


class EnvironmentProviderOutcomeCertainty(StrEnum):
    NOT_DISPATCHED = "not_dispatched"
    KNOWN = "known"
    UNKNOWN = "unknown"


class EnvironmentProviderRecoveryHint(StrEnum):
    NONE = "none"
    FIX_INPUT = "fix_input"
    REFRESH_RUNTIME = "refresh_runtime"
    RETRY_SAME_OPERATION = "retry_same_operation"
    RECONCILE = "reconcile"


class EnvironmentProviderErrorContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_key: str | None = Field(default=None, max_length=128)
    schema_version: str | None = Field(default=None, max_length=64)
    state_version: str | None = Field(default=None, max_length=64)
    action: str | None = Field(default=None, max_length=64)
    operation_id: str | None = Field(default=None, max_length=128)
    resource_correlation: str | None = Field(default=None, max_length=128)
    distribution_name: str | None = Field(default=None, max_length=200)
    distribution_version: str | None = Field(default=None, max_length=100)

    @field_validator(
        "provider_key",
        "schema_version",
        "state_version",
        "action",
        "operation_id",
        "resource_correlation",
        "distribution_name",
        "distribution_version",
    )
    @classmethod
    def _bounded_non_blank(cls, value: str | None) -> str | None:
        if value is not None and (not value or value != value.strip()):
            raise ValueError("error context values must be non-blank and trimmed")
        return value


class EnvironmentProviderSafeError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1, max_length=128)
    category: EnvironmentProviderErrorCategory
    certainty: EnvironmentProviderOutcomeCertainty
    message: str = Field(min_length=1, max_length=256)
    recovery_hint: EnvironmentProviderRecoveryHint
    context: EnvironmentProviderErrorContext


_SAFE_MESSAGES = {
    EnvironmentProviderErrorCategory.INVALID: "Environment provider input is invalid.",
    EnvironmentProviderErrorCategory.UNSUPPORTED: "Environment provider operation is unsupported.",
    EnvironmentProviderErrorCategory.MISSING: "Environment provider target is unavailable.",
    EnvironmentProviderErrorCategory.DENIED: "Environment provider operation was denied.",
    EnvironmentProviderErrorCategory.CONFLICT: "Environment provider operation conflicts with current state.",
    EnvironmentProviderErrorCategory.UNAVAILABLE: "Environment provider is unavailable.",
    EnvironmentProviderErrorCategory.TIMEOUT: "Environment provider operation timed out safely.",
    EnvironmentProviderErrorCategory.UNKNOWN_OUTCOME: "Environment provider outcome is unknown.",
    EnvironmentProviderErrorCategory.CLEANUP: "Environment provider cleanup failed.",
    EnvironmentProviderErrorCategory.PROVIDER_FAILURE: "Environment provider operation failed.",
}


class EnvironmentProviderError(Exception):
    """Public provider failure with rich local diagnostics and a safe projection."""

    def __init__(
        self,
        description: str,
        *,
        code: str,
        category: EnvironmentProviderErrorCategory,
        certainty: EnvironmentProviderOutcomeCertainty,
        recovery_hint: EnvironmentProviderRecoveryHint = EnvironmentProviderRecoveryHint.NONE,
        context: EnvironmentProviderErrorContext | None = None,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        if not description or len(description) > 4096:
            raise ValueError("provider error descriptions must be non-empty and bounded")
        if not _CODE_PATTERN.fullmatch(code):
            raise ValueError("provider error codes must be bounded stable identifiers")
        try:
            detached_details = detach_json_object(details or {})
        except JsonBoundaryError as exc:
            raise ValueError("provider error details must be finite JSON") from exc
        self.code = code
        self.category = category
        self.certainty = certainty
        self.description = description
        self.recovery_hint = recovery_hint
        self.context = context or EnvironmentProviderErrorContext()
        self.details = detached_details
        super().__init__(description)

    def safe_projection(self) -> EnvironmentProviderSafeError:
        return EnvironmentProviderSafeError(
            code=self.code,
            category=self.category,
            certainty=self.certainty,
            message=_SAFE_MESSAGES[self.category],
            recovery_hint=self.recovery_hint,
            context=self.context,
        )
