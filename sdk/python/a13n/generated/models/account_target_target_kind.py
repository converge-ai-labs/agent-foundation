from enum import StrEnum


class AccountTargetTargetKind(StrEnum):
    CONVERSATION = "conversation"
    REPOSITORY = "repository"

    def __str__(self) -> str:
        return str(self.value)
