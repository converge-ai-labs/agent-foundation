from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import EnvironmentState

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
    state_version: str | None = Field(default=None, max_length=64)
    action: str | None = Field(default=None, max_length=64)
    operation_id: str | None = Field(default=None, max_length=128)
    resource_correlation: str | None = Field(default=None, max_length=128)
    distribution_name: str | None = Field(default=None, max_length=200)
    distribution_version: str | None = Field(default=None, max_length=100)

    @field_validator(
        "provider_key",
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


class EnvironmentManagementError(EnvironmentProviderError):
    """A management failure with authoritative state observed before it failed."""

    def __init__(
        self, error: EnvironmentProviderError, state: EnvironmentState | None, operation_id: str, environment_id: str
    ) -> None:
        super().__init__(
            error.description,
            code=error.code,
            category=error.category,
            certainty=error.certainty,
            recovery_hint=error.recovery_hint,
            details=error.details,
            context=error.context.model_copy(
                update={"operation_id": operation_id, "resource_correlation": environment_id}
            ),
        )
        self.state = state.model_copy(deep=True) if state is not None else None


class EnvironmentManagementCancelled(asyncio.CancelledError):
    """Cancellation retaining state already observed by a management operation."""

    def __init__(self, state: EnvironmentState | None, operation_id: str) -> None:
        super().__init__("Environment management operation cancelled")
        self.state = state
        self.operation_id = operation_id


def observed_environment_state(error: BaseException, fallback: EnvironmentState | None) -> EnvironmentState | None:
    """Recover observed state through a timeout's cancellation cause."""
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, (EnvironmentManagementError, EnvironmentManagementCancelled)):
            return current.state
        current = current.__cause__ or current.__context__
    return fallback


def provider_error(
    provider_key: str,
    code: str,
    category: EnvironmentProviderErrorCategory,
    *,
    certainty: EnvironmentProviderOutcomeCertainty = EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
    recovery_hint: EnvironmentProviderRecoveryHint | None = None,
    description: str | None = None,
) -> EnvironmentProviderError:
    """Build a typed Provider failure; the default hint follows category and certainty."""
    if recovery_hint is None:
        recovery_hint = (
            EnvironmentProviderRecoveryHint.RECONCILE
            if certainty is EnvironmentProviderOutcomeCertainty.UNKNOWN
            else EnvironmentProviderRecoveryHint.REFRESH_RUNTIME
            if category in {EnvironmentProviderErrorCategory.UNAVAILABLE, EnvironmentProviderErrorCategory.TIMEOUT}
            else EnvironmentProviderRecoveryHint.FIX_INPUT
        )
    return EnvironmentProviderError(
        description or _SAFE_MESSAGES[category],
        code=code,
        category=category,
        certainty=certainty,
        recovery_hint=recovery_hint,
        context=EnvironmentProviderErrorContext(provider_key=provider_key),
    )


# Operation diagnostics are separate from provider lifecycle failures above. These
# messages are code-owned; exception descriptions remain local diagnostics only.
_OPERATION_MESSAGES: dict[str, tuple[str, str]] = {
    "environment_request_invalid": (
        "Environment operation input is invalid.",
        "Check the operation schema and the indicated field, then correct the request before retrying.",
    ),
    "environment_edit_not_found": (
        "The text selected for replacement was not found.",
        "Read the current target and copy an exact old_string, including whitespace, before retrying the edit.",
    ),
    "environment_edit_ambiguous": (
        "The text selected for replacement occurs more than once.",
        "Add enough surrounding context to select one match, or explicitly request replacement of every occurrence.",
    ),
    "environment_reference_invalid": (
        "The Environment resource reference is invalid.",
        "Use a reference returned by the current execution; do not invent process or output references.",
    ),
    "environment_reference_stale": (
        "The Environment resource reference is stale.",
        "Inspect current resources through the Host; do not reuse references from a replaced target or earlier execution.",
    ),
    "environment_cursor_invalid": (
        "The Environment continuation offset is invalid.",
        "Use the returned continuation offset and keep the original filters unchanged.",
    ),
    "environment_not_found": (
        "The selected resource was not found or is not visible.",
        "Verify the file path or resource reference in the selected environment. List an existing parent directory.",
    ),
    "environment_denied": (
        "The selected Environment does not permit this operation.",
        "Check the selected environment and its permissions; do not switch authority to bypass a denial.",
    ),
    "environment_selection_invalid": (
        "The Environment selection is invalid.",
        "Use a path or resource available in the selected environment.",
    ),
    "environment_unsupported": (
        "This operation or option is not supported by the selected Environment.",
        "Check the selected environment's advertised operations and supported options.",
    ),
    "environment_too_large": (
        "The operation exceeded an Environment limit.",
        "Narrow the operation or reduce the requested page size. For mutations, inspect the outcome before repeating.",
    ),
    "environment_timeout": (
        "The Environment operation timed out.",
        "Inspect available status and effect evidence before deciding whether to retry.",
    ),
    "environment_unknown_outcome": (
        "The Environment operation may have taken effect; its outcome is unknown.",
        "Reconcile current state or operation evidence before repeating any side effect.",
    ),
    "environment_conflict": (
        "The operation conflicts with current Environment state.",
        "Inspect current state and update the request; do not blindly repeat a mutation.",
    ),
    "environment_busy": (
        "The Environment cannot admit the operation while it is busy.",
        "Wait for current work or inspect its status before making another request.",
    ),
    "environment_stale_mount": (
        "The Environment reference no longer identifies the current target.",
        "Refresh the Environment through the Host and obtain current references; do not reuse stale process IDs.",
    ),
    "environment_closed": (
        "The Environment operation scope is closed.",
        "Open a new execution through the connector after reconciling any outstanding work.",
    ),
    "environment_unavailable": (
        "The selected Environment is unavailable.",
        "Check Environment readiness with the Host. Reconcile any previously dispatched work before retrying.",
    ),
    "environment_cancelled": (
        "The Environment operation was cancelled.",
        "Inspect retained state or operation evidence before repeating a mutation.",
    ),
    "environment_provider_failure": (
        "The Environment provider could not complete the operation.",
        "Check provider readiness and Host diagnostics; reconcile possible effects before retrying.",
    ),
}


def operation_error_projection(
    code: str,
    *,
    details: Mapping[str, JsonValue],
    retry_hint: str | None,
) -> dict[str, JsonValue]:
    """Project only bounded, explicitly public operation diagnostics, never causes."""
    if not _CODE_PATTERN.fullmatch(code):
        code = "environment_provider_failure"
    message, hint = _OPERATION_MESSAGES.get(code, _OPERATION_MESSAGES["environment_provider_failure"])
    safe: dict[str, JsonValue] = {"hint": hint}
    for key, limit in (("field", 128), ("reason", 128), ("hint", 1024)):
        value = details.get(key)
        if isinstance(value, str) and value.strip():
            safe[key] = value[:limit]
    for key in ("timeout_seconds", "edit_index", "occurrences", "emitted_items", "produced_bytes", "dropped_items"):
        value = details.get(key)
        if (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and 0 <= value <= 2**64 - 1
            and (isinstance(value, int) or math.isfinite(value))
        ):
            safe[key] = value
    missing = details.get("missing")
    if isinstance(missing, list) and all(isinstance(value, str) for value in missing):
        safe["missing"] = [value[:128] for value in missing[:32] if isinstance(value, str)]
    # Typed protocol evidence is retained without native IDs, receipts, output or
    # arbitrary safe_detail text from an external provider.
    stage = details.get("dispatch_stage")
    if stage in ("pre_dispatch", "dispatching", "dispatched", "completed", "unknown"):
        safe["dispatch_stage"] = stage
    protocol_retry = details.get("provider_retry_hint")
    if protocol_retry in ("never", "same_request", "after_refresh", "after_capacity", "reconcile_first"):
        safe["provider_retry_hint"] = protocol_retry
    result: dict[str, JsonValue] = {"code": code, "message": message, "details": safe}
    if retry_hint:
        result["retry_hint"] = retry_hint[:128]
    return result
