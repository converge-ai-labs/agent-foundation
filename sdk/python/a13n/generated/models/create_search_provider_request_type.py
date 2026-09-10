from enum import StrEnum


class CreateSearchProviderRequestType(StrEnum):
    BRAVE = "brave"
    EXA = "exa"

    def __str__(self) -> str:
        return str(self.value)
