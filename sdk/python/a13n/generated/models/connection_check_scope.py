from enum import StrEnum


class ConnectionCheckScope(StrEnum):
    MCP_DISCOVERY = "mcp_discovery"
    PROVIDER_ACCOUNT = "provider_account"

    def __str__(self) -> str:
        return str(self.value)
