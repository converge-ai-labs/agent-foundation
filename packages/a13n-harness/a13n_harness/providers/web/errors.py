"""Explicit retry-safe Web response failures."""

import math

from .contracts import WebProviderError


class WebProviderResponseError(WebProviderError):
    """Safe explicit provider response that can be eligible for bounded retry."""

    def __init__(self, code: str, *, retry_after: float | None = None) -> None:
        if retry_after is not None and (
            type(retry_after) not in {int, float} or math.isnan(retry_after) or retry_after < 0
        ):
            raise ValueError("retry_after must be a non-negative number")
        super().__init__(code)
        self.retry_after = retry_after
