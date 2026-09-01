"""Safe Trace Query failures."""

from __future__ import annotations

from typing import Literal

ProviderFailure = Literal[
    "unavailable",
    "version_unsupported",
    "filter_unsupported",
    "malformed",
    "response_too_large",
]


class TraceQueryProviderError(Exception):
    def __init__(self, failure: ProviderFailure) -> None:
        super().__init__(failure)
        self.failure = failure


class TraceQueryError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
