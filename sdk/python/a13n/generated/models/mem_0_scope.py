from enum import StrEnum


class Mem0Scope(StrEnum):
    AGENT = "agent"
    THREAD = "thread"
    USER = "user"

    def __str__(self) -> str:
        return str(self.value)
