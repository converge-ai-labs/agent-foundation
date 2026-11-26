"""Harness client foundation for Agent Client Protocol sessions."""

from .client import AcpEnvironmentClient, AcpRunBinding, AcpUpdate, ConfigOption
from .mcp import AcpHostToolBridge, AcpHostToolScope, acp_host_tool_name
from .session import AcpSessionRuntime, AcpSessionSetup, PromptBlock, SteeringOutcome, connect_streams
from .transport import (
    AcpChannel,
    AcpError,
    AcpTransport,
    LocalStdioTransport,
    StreamChannel,
)

__all__ = [
    "AcpChannel",
    "AcpEnvironmentClient",
    "AcpError",
    "AcpHostToolBridge",
    "AcpHostToolScope",
    "AcpRunBinding",
    "AcpSessionRuntime",
    "AcpSessionSetup",
    "AcpTransport",
    "AcpUpdate",
    "ConfigOption",
    "LocalStdioTransport",
    "PromptBlock",
    "SteeringOutcome",
    "StreamChannel",
    "acp_host_tool_name",
    "connect_streams",
]
