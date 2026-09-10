from enum import StrEnum


class EnvironmentOwnership(StrEnum):
    EXTERNAL = "external"
    MANAGED = "managed"

    def __str__(self) -> str:
        return str(self.value)
