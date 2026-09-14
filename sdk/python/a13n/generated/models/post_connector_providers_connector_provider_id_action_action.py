from enum import StrEnum


class PostConnectorProvidersConnectorProviderIdActionAction(StrEnum):
    DISABLE = "disable"
    ENABLE = "enable"

    def __str__(self) -> str:
        return str(self.value)
