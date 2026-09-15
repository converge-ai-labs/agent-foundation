from enum import StrEnum


class ClientToolDefinitionPermission(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    INHERIT = "inherit"

    def __str__(self) -> str:
        return str(self.value)
