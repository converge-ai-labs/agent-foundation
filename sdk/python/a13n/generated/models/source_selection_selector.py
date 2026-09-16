from enum import StrEnum


class SourceSelectionSelector(StrEnum):
    CURRENT = "current"
    EMPTY = "empty"
    EXPLICIT = "explicit"

    def __str__(self) -> str:
        return str(self.value)
