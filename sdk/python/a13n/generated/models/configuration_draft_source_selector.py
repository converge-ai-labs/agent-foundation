from enum import StrEnum


class ConfigurationDraftSourceSelector(StrEnum):
    CURRENT = "current"
    EMPTY = "empty"
    EXPLICIT = "explicit"

    def __str__(self) -> str:
        return str(self.value)
