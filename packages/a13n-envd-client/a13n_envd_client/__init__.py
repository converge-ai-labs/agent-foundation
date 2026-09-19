"""Low-level Python client package for the Agent Environment Interaction Protocol."""

from importlib.metadata import PackageNotFoundError, version

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
from .file_transfer import EIPFileReader, EIPFileWriter
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

__all__ = [
    "AcceptedWebSocketTransport",
    "ControlFrame",
    "EIPClientError",
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
