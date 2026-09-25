"""One detached error contract shared by Service operations and HTTP."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Literal

from pydantic import JsonValue

IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"

type ErrorCode = Literal[
    "invalid_argument",
    "invalid_cursor",
    "unauthenticated",
    "forbidden",
    "not_found",
    "already_exists",
    "conflict",
    "precondition_failed",
    "precondition_required",
    "payload_too_large",
    "request_timeout",
    "disabled",
    "unavailable",
    "rate_limited",
    "internal",
]


class ServiceError(Exception):
    def __init__(self, code: ErrorCode, message: str, details: Mapping[str, JsonValue] | None = None):
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message
        self.details = dict(details or {})


def not_found(kind: str, resource_id: str) -> ServiceError:
    return ServiceError("not_found", f"{kind} {resource_id} not found", {"kind": kind, "id": resource_id})


def conflict(kind: str, resource_id: str, reason: str, **details: JsonValue) -> ServiceError:
    """A state rule refused the operation; `reason` is a stable machine-readable word, `details` what it bounds."""
    message = f"{kind} {resource_id}: {reason.replace('_', ' ')}"
    return ServiceError("conflict", message, {"kind": kind, "id": resource_id, "reason": reason, **details})


def disabled(kind: str, resource_id: str) -> ServiceError:
    return ServiceError("disabled", f"{kind} {resource_id} is disabled", {"kind": kind, "id": resource_id})


def invalid(field: str, reason: str) -> ServiceError:
    return ServiceError("invalid_argument", f"{field}: {reason}", {"field": field, "reason": reason})


# The `rate_limited` detail HTTP also sends as `Retry-After`.
RETRY_AFTER = "retry_after_seconds"


def rate_limited(
    message: str, retry_after_seconds: int, details: Mapping[str, JsonValue] | None = None
) -> ServiceError:
    return ServiceError("rate_limited", message, {RETRY_AFTER: retry_after_seconds, **(details or {})})


@contextmanager
def at_field(path: str) -> Iterator[None]:
    """A referenced resource that is missing, disabled or unusable makes the field at `path` invalid.

    The details keep the resource's kind and ID; a refused permission stays `forbidden`.
    """
    try:
        yield
    except ServiceError as error:
        if error.code == "forbidden":
            raise ServiceError(error.code, error.message, {**error.details, "field": path}) from None
        reason = str(error.details.get("reason") or error.message)
        raise ServiceError(
            "invalid_argument", f"{path}: {reason}", {**error.details, "field": path, "reason": reason}
        ) from None
