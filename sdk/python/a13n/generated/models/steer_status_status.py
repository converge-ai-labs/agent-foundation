from enum import StrEnum


class SteerStatusStatus(StrEnum):
    CONSUMED = "consumed"
    PENDING = "pending"
    SUPERSEDED = "superseded"

    def __str__(self) -> str:
        return str(self.value)
