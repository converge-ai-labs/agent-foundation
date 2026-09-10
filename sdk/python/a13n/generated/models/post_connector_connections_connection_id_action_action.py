from enum import StrEnum


class PostConnectorConnectionsConnectionIdActionAction(StrEnum):
    DISABLE = "disable"
    ENABLE = "enable"

    def __str__(self) -> str:
        return str(self.value)
