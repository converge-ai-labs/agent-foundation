from enum import StrEnum


class AgentSource(StrEnum):
    BUILTIN = "builtin"
    CUSTOM = "custom"

    def __str__(self) -> str:
        return str(self.value)
