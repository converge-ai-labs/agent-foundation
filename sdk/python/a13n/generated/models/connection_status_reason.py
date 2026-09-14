from enum import StrEnum


class ConnectionStatusReason(StrEnum):
    INCOMPATIBLE = "incompatible"
    REAUTHORIZATION_REQUIRED = "reauthorization_required"

    def __str__(self) -> str:
        return str(self.value)
