from enum import StrEnum


class ObservationType(StrEnum):
    EVENT = "event"
    GENERATION = "generation"
    SPAN = "span"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return str(self.value)
