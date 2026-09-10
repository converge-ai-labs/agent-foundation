from enum import StrEnum


class EnvironmentCommandAction(StrEnum):
    DELETE = "delete"
    STOP = "stop"

    def __str__(self) -> str:
        return str(self.value)
