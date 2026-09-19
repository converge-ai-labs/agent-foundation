"""Shared validation for configurable Hook destinations."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Protocol

from a13n_harness.providers.endpoint_policy import EndpointPolicyError
from anyio import fail_after


class EndpointValidator(Protocol):
    def validate(self, endpoint: str, *, resolve_dns: bool = True) -> Awaitable[str]: ...


class HookEndpointValidationError(ValueError):
    """A Webhook endpoint failed bounded outbound-policy validation."""


async def validate_hook_endpoint(
    validator: EndpointValidator,
    endpoint_url: str,
    *,
    timeout_seconds: float,
) -> None:
    if timeout_seconds <= 0:
        raise ValueError("Hook endpoint validation timeout must be positive")
    try:
        with fail_after(timeout_seconds):
            await validator.validate(endpoint_url, resolve_dns=True)
    except (EndpointPolicyError, TimeoutError) as error:
        raise HookEndpointValidationError("The Webhook endpoint is not allowed") from error


__all__ = ["EndpointValidator", "HookEndpointValidationError", "validate_hook_endpoint"]
