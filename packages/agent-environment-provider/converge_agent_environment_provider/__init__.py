"""Shared environment-provider boundary for Converge agents."""

from importlib.metadata import PackageNotFoundError, version

from .attachments import (
    AcceptedWebSocketEIPSessionSource,
    DirectLocalEnvironmentAttachment,
    EIPEnvironmentAttachment,
    EIPSessionSource,
    EnvironmentRuntimeAttachment,
    HttpEIPSessionSource,
    StdioEIPSessionSource,
)

try:
    __version__ = version("converge-agent-environment-provider")
except PackageNotFoundError:  # pragma: no cover - source-tree imports without installation
    __version__ = "0.0.0"

__all__ = [
    "AcceptedWebSocketEIPSessionSource",
    "DirectLocalEnvironmentAttachment",
    "EIPEnvironmentAttachment",
    "EIPSessionSource",
    "EnvironmentRuntimeAttachment",
    "HttpEIPSessionSource",
    "StdioEIPSessionSource",
    "__version__",
]
