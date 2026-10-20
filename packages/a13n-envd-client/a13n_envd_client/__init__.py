"""Low-level Python client package for the Agent Environment Interaction Protocol."""
# `_EXPORTS` owns the surface; these imports serve type checkers.

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .errors import (
        EIPClientError,
        EIPConnectionError,
        EIPMethodError,
        EIPProtocolError,
        EIPRequestTimeoutError,
        EIPSessionStateError,
        EIPTransferError,
        EIPTransportClosedError,
        EIPTransportError,
    )
    from .file_transfer import EIPComputerObservationReader, EIPFileReader, EIPFileWriter
    from .http import HttpTransport, normalize_http_endpoint
    from .output import EIPOutputPage, EIPOutputReader
    from .requester import RequestCoordinator, SessionRequester
    from .session import EIPDeviceConnection, EIPSession
    from .stdio import StdioTransport
    from .transport import ControlFrame, EIPTransport, EIPTransportFrame
    from .websocket import AcceptedWebSocketTransport, WebSocketConnection

try:
    __version__ = version("a13n-envd-client")
except PackageNotFoundError:  # pragma: no cover - source-tree imports without installation
    __version__ = "0.0.0"

_EXPORTS = {
    "errors": (
        "EIPClientError",
        "EIPConnectionError",
        "EIPMethodError",
        "EIPProtocolError",
        "EIPRequestTimeoutError",
        "EIPSessionStateError",
        "EIPTransferError",
        "EIPTransportClosedError",
        "EIPTransportError",
    ),
    "file_transfer": ("EIPComputerObservationReader", "EIPFileReader", "EIPFileWriter"),
    "http": ("HttpTransport", "normalize_http_endpoint"),
    "output": ("EIPOutputPage", "EIPOutputReader"),
    "requester": ("RequestCoordinator", "SessionRequester"),
    "session": ("EIPDeviceConnection", "EIPSession"),
    "stdio": ("StdioTransport",),
    "transport": ("ControlFrame", "EIPTransport", "EIPTransportFrame"),
    "websocket": ("AcceptedWebSocketTransport", "WebSocketConnection"),
}

__all__ = [
    "AcceptedWebSocketTransport",
    "ControlFrame",
    "EIPClientError",
    "EIPComputerObservationReader",
    "EIPConnectionError",
    "EIPDeviceConnection",
    "EIPFileReader",
    "EIPFileWriter",
    "EIPMethodError",
    "EIPOutputPage",
    "EIPOutputReader",
    "EIPProtocolError",
    "EIPRequestTimeoutError",
    "EIPSession",
    "EIPSessionStateError",
    "EIPTransferError",
    "EIPTransport",
    "EIPTransportClosedError",
    "EIPTransportError",
    "EIPTransportFrame",
    "HttpTransport",
    "RequestCoordinator",
    "SessionRequester",
    "StdioTransport",
    "WebSocketConnection",
    "__version__",
    "normalize_http_endpoint",
]


def __getattr__(name: str) -> Any:
    for module, names in _EXPORTS.items():
        if name in names:
            value = getattr(import_module(f".{module}", __name__), name)
            globals()[name] = value
            return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
