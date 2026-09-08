from __future__ import annotations

from typing import Any

_DEFAULT_RESPONSE_ALLOWANCE = 30.0


def response_timeout(params: dict[str, Any], configured_timeout: float | None) -> float | None:
    """Allow the remote operation budget plus time to exchange its response."""
    context = params.get("context")
    timeout_ms = context.get("timeout_ms") if isinstance(context, dict) else None
    if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool):
        return configured_timeout
    allowance = configured_timeout if configured_timeout is not None else _DEFAULT_RESPONSE_ALLOWANCE
    return timeout_ms / 1000 + allowance
