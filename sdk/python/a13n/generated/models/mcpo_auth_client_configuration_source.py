from enum import StrEnum


class MCPOAuthClientConfigurationSource(StrEnum):
    DYNAMIC = "dynamic"
    METADATA_DOCUMENT = "metadata_document"
    PRE_REGISTERED = "pre_registered"

    def __str__(self) -> str:
        return str(self.value)
