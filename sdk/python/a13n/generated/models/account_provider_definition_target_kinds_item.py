from enum import StrEnum


class AccountProviderDefinitionTargetKindsItem(StrEnum):
    CONVERSATION = "conversation"
    REPOSITORY = "repository"

    def __str__(self) -> str:
        return str(self.value)
