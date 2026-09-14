from enum import StrEnum


class GrantResourceType(StrEnum):
    ORGANIZATION = "organization"
    WORKSPACE = "workspace"

    def __str__(self) -> str:
        return str(self.value)
