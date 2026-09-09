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
    "CreateSearchProviderRequest",
    "Page",
    "ProtocolError",
    "Representation",
    "SearchProvider",
    "SearchProviderDefinition",
    "SearchProviderReference",
    "SearchProviderTestResult",
    "SearchScope",
    "SearchSelection",
    "TransportError",
    "UpdateSearchProviderRequest",
    "__version__",
]

from .client import ApiError, Client, ProtocolError, SearchScope, TransportError
from .models import (
    AgentConfig,
    AgentRunOverride,
    CreateSearchProviderRequest,
    Page,
    Representation,
    SearchProvider,
    SearchProviderDefinition,
    SearchProviderReference,
    SearchProviderTestResult,
    SearchSelection,
    UpdateSearchProviderRequest,
)
