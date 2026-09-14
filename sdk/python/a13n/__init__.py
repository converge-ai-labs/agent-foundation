"""Python SDK package for a13n Service."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("a13n")
except PackageNotFoundError:  # pragma: no cover - source-tree imports without installation
    __version__ = "0.0.0"

__all__ = [
    "AgentConfig",
    "AgentRunOverride",
    "ApiError",
    "Client",
    "CreateWebProviderRequest",
    "DownloadToolConfiguration",
    "FetchToolConfiguration",
    "Page",
    "ProtocolError",
    "Representation",
    "ScrapeToolConfiguration",
    "SearchToolConfiguration",
    "ToolSelection",
    "ToolsetSelection",
    "TransportError",
    "UpdateWebProviderRequest",
    "WebProvider",
    "WebProviderDefinition",
    "WebProviderReference",
    "WebProviderScope",
    "WebProviderTestResult",
    "WorkspaceClient",
    "__version__",
]

from .client import ApiError, Client, ProtocolError, TransportError, WebProviderScope, WorkspaceClient
from .models import (
    AgentConfig,
    AgentRunOverride,
    CreateWebProviderRequest,
    DownloadToolConfiguration,
    FetchToolConfiguration,
    Page,
    Representation,
    ScrapeToolConfiguration,
    SearchToolConfiguration,
    ToolSelection,
    ToolsetSelection,
    UpdateWebProviderRequest,
    WebProvider,
    WebProviderDefinition,
    WebProviderReference,
    WebProviderTestResult,
)
