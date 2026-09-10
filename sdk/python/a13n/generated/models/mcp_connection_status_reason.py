from enum import StrEnum


class MCPConnectionStatusReason(StrEnum):
    INCOMPATIBLE = "incompatible"
    REAUTHORIZATION_REQUIRED = "reauthorization_required"

    def __str__(self) -> str:
        return str(self.value)
