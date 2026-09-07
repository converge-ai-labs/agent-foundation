"""ConnectorProvider and ConnectorConnection management."""

from .contracts import ConnectorConnectionRuntime, ConnectorProviderRuntime
from .domain import (
    Connector,
    ConnectorCollection,
    ConnectorConnection,
    ConnectorConnectionCollection,
    ConnectorConnectionStatus,
    ConnectorConnectionStatusReason,
    ConnectorProvider,
    ConnectorProviderCollection,
    ConnectorProviderStatus,
)
from .registry import ConnectorProviderDefinition, ConnectorProviderImplementation, ConnectorProviderRegistry

__all__ = [
    "Connector",
    "ConnectorCollection",
    "ConnectorConnection",
    "ConnectorConnectionCollection",
    "ConnectorConnectionRuntime",
    "ConnectorConnectionStatus",
    "ConnectorConnectionStatusReason",
    "ConnectorProvider",
    "ConnectorProviderCollection",
    "ConnectorProviderDefinition",
    "ConnectorProviderImplementation",
    "ConnectorProviderRegistry",
    "ConnectorProviderRuntime",
    "ConnectorProviderStatus",
]
