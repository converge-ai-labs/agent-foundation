"""Safe Trace Query failures."""

from __future__ import annotations

from typing import Literal

from a13n_service.public_errors import PublicError

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


class TraceQueryError(PublicError):
    pass
