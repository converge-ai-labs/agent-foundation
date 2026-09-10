from enum import StrEnum


class TargetConfigTargetKind(StrEnum):
    CONVERSATION = "conversation"
    REPOSITORY = "repository"

    def __str__(self) -> str:
        return str(self.value)
