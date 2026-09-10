from enum import StrEnum


class ThreadRole(StrEnum):
    CHILD = "child"
    ROOT = "root"

    def __str__(self) -> str:
        return str(self.value)
