"""Explicit retry-safe Web response failures."""

import math

from .contracts import WebProviderError


class WebProviderResponseError(WebProviderError):
    """Safe explicit provider response that can be eligible for bounded retry."""

    def __init__(self, code: str, *, retry_after_seconds: float | None = None) -> None:
        if retry_after_seconds is not None and (
            type(retry_after_seconds) not in {int, float} or math.isnan(retry_after_seconds) or retry_after_seconds < 0
        ):
            raise ValueError("retry_after_seconds must be a non-negative number")
        super().__init__(code)
        self.retry_after_seconds = retry_after_seconds
