from enum import StrEnum


class MCPOAuthDiscoveryClientRegistration(StrEnum):
    DYNAMIC = "dynamic"
    MANUAL = "manual"

    def __str__(self) -> str:
        return str(self.value)
