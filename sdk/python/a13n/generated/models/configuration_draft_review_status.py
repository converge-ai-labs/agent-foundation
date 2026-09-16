from enum import StrEnum


class ConfigurationDraftReviewStatus(StrEnum):
    APPLIED = "applied"
    DISCARDED = "discarded"
    EXPIRED = "expired"
    OPEN = "open"

    def __str__(self) -> str:
        return str(self.value)
