from enum import StrEnum


class ConfigurationDraftReviewMode(StrEnum):
    CREATE = "create"
    UPDATE = "update"

    def __str__(self) -> str:
        return str(self.value)
