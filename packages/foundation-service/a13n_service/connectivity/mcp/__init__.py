"""Remote MCP connection management and bounded protocol discovery."""

from .domain import (
    MCPAuthMode,
    MCPConnection,
    MCPConnectionStatus,
    MCPConnectionStatusReason,
    MCPTool,
)
from .protocol import MCPDiscovery, MCPProtocolClient, MCPProtocolError

__all__ = [
    "MCPAuthMode",
    "MCPConnection",
    "MCPConnectionStatus",
    "MCPConnectionStatusReason",
    "MCPDiscovery",
    "MCPProtocolClient",
    "MCPProtocolError",
    "MCPTool",
]
