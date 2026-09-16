from enum import StrEnum


class ConfigurationApplicationReceiptReviewedMode(StrEnum):
    CREATE = "create"
    UPDATE = "update"

    def __str__(self) -> str:
        return str(self.value)
