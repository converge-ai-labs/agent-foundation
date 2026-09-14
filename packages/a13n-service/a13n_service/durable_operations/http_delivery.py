"""Common HTTP acknowledgement and retry rules for durable deliveries."""

from dataclasses import dataclass

import httpx2


@dataclass(frozen=True, slots=True)
class DeliveryFailure:
    error_code: str
    retryable: bool


async def response_is_bounded(response: httpx2.Response, *, maximum_bytes: int) -> bool:
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > maximum_bytes:
                return False
        except ValueError:
            return False
    received = 0
    async for chunk in response.aiter_bytes():
        received += len(chunk)
        if received > maximum_bytes:
            return False
    return True


def http_failure(status_code: int, *, error_prefix: str) -> DeliveryFailure | None:
    if 200 <= status_code < 300:
        return None
    return DeliveryFailure(
        f"{error_prefix}_http_{status_code}",
        retryable=status_code in {408, 425, 429} or status_code >= 500,
    )


def retry_delay_seconds(attempt_count: int, *, base: float, maximum: float) -> float:
    exponent = min(max(attempt_count - 1, 0), 16)
    return min(maximum, base * (2**exponent))
