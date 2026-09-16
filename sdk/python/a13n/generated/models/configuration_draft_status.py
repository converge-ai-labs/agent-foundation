from enum import StrEnum


class ConfigurationDraftStatus(StrEnum):
    DISCARDED = "discarded"
    EXPIRED = "expired"
    OPEN = "open"

    def __str__(self) -> str:
        return str(self.value)
