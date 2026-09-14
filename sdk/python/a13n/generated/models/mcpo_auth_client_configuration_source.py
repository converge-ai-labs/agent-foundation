from enum import StrEnum


class MCPOAuthClientConfigurationSource(StrEnum):
    DYNAMIC = "dynamic"
    PRE_REGISTERED = "pre_registered"

    def __str__(self) -> str:
        return str(self.value)
