"""Foundation Service public protocol gateway."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .runtime import GatewayRuntime


def __getattr__(name: str) -> Any:
    if name == "GatewayRuntime":
        from .runtime import GatewayRuntime

        return GatewayRuntime
    raise AttributeError(name)


__all__ = ["GatewayRuntime"]
