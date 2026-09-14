from enum import StrEnum


class MCPServerOrigin(StrEnum):
    BUILTIN = "builtin"
    DEPLOYMENT = "deployment"

    def __str__(self) -> str:
        return str(self.value)
