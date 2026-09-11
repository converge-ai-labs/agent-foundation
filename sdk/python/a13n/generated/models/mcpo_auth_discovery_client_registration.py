from enum import StrEnum


class MCPOAuthDiscoveryClientRegistration(StrEnum):
    DYNAMIC = "dynamic"
    MANUAL = "manual"
    METADATA_DOCUMENT = "metadata_document"

    def __str__(self) -> str:
        return str(self.value)
