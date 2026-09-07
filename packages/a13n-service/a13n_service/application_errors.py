"""Safe application failures independent of HTTP and protocol envelopes."""

from __future__ import annotations

from enum import StrEnum


class ErrorCategory(StrEnum):
    invalid_request = "invalid_request"
    unauthenticated = "unauthenticated"
    forbidden = "forbidden"
    not_found = "not_found"
    not_acceptable = "not_acceptable"
    conflict = "conflict"
    stale_version = "stale_version"
    size_limit = "size_limit"
    unsupported_media = "unsupported_media"
    invalid_input = "invalid_input"
    rate_limited = "rate_limited"
    internal = "internal"
    dependency_failure = "dependency_failure"
    unavailable = "unavailable"
    timeout = "timeout"


class ApplicationError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        category: ErrorCategory,
        details: dict[str, object] | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.category = category
        self.details = details or {}
        self.retry_after_seconds = retry_after_seconds
