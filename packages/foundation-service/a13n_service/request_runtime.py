"""Typed process-runtime access at the HTTP transport boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

from a13n_service.process.runtime import ServiceRuntime

if TYPE_CHECKING:
    from a13n_service.connectivity.runtime import ConnectivityControlRuntime, ConnectivityDataRuntime
    from a13n_service.process.runtime import ControlRuntime


def get_service_runtime(request: Request) -> ServiceRuntime | None:
    """Return the initialized process runtime at the transport boundary."""

    runtime = getattr(request.app.state, "runtime", None)
    return runtime if isinstance(runtime, ServiceRuntime) else None


def get_control_runtime(request: Request) -> ControlRuntime | None:
    """Return Control-plane capabilities when the selected role owns them."""

    runtime = get_service_runtime(request)
    return None if runtime is None else runtime.control


def get_connectivity_control_runtime(request: Request) -> ConnectivityControlRuntime | None:
    """Return Connectivity management capabilities when owned by this role."""

    runtime = get_service_runtime(request)
    if runtime is None or runtime.connectivity is None:
        return None
    return runtime.connectivity.control


def get_connectivity_data_runtime(request: Request) -> ConnectivityDataRuntime | None:
    """Return Connectivity ingress capabilities when owned by this role."""

    runtime = get_service_runtime(request)
    if runtime is None or runtime.connectivity is None:
        return None
    return runtime.connectivity.data


__all__ = [
    "get_connectivity_control_runtime",
    "get_connectivity_data_runtime",
    "get_control_runtime",
    "get_service_runtime",
]
