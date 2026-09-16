from enum import StrEnum


class CreateDocumentKind(StrEnum):
    DAILY = "daily"
    LONG_TERM = "long_term"

    def __str__(self) -> str:
        return str(self.value)
