from enum import StrEnum


class MemoryScope(StrEnum):
    AGENT = "agent"
    THREAD = "thread"
    USER = "user"

    def __str__(self) -> str:
        return str(self.value)
