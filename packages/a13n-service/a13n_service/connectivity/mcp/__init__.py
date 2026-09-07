"""Remote MCP connection management and bounded protocol discovery."""

from .domain import (
    MCPAuthMode,
    MCPConnection,
    MCPConnectionStatus,
    MCPConnectionStatusReason,
    MCPTool,
)

__all__ = [
    "MCPAuthMode",
    "MCPConnection",
    "MCPConnectionStatus",
    "MCPConnectionStatusReason",
    "MCPTool",
]
