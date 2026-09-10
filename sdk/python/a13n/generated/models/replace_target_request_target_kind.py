from enum import StrEnum


class ReplaceTargetRequestTargetKind(StrEnum):
    CONVERSATION = "conversation"
    REPOSITORY = "repository"

    def __str__(self) -> str:
        return str(self.value)
