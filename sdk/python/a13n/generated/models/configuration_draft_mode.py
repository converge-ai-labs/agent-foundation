from enum import StrEnum


class ConfigurationDraftMode(StrEnum):
    CREATE = "create"
    UPDATE = "update"

    def __str__(self) -> str:
        return str(self.value)
