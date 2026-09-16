from enum import StrEnum


class DocumentEntryKind(StrEnum):
    DAILY = "daily"
    LONG_TERM = "long_term"

    def __str__(self) -> str:
        return str(self.value)
