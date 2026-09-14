from enum import StrEnum


class ClientToolDefinitionPermission(StrEnum):
    ALLOW = "allow"
    AUTO = "auto"
    DENY = "deny"

    def __str__(self) -> str:
        return str(self.value)
