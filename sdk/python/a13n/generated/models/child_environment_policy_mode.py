from enum import StrEnum


class ChildEnvironmentPolicyMode(StrEnum):
    DEDICATED = "dedicated"
    NONE = "none"
    SHARED = "shared"

    def __str__(self) -> str:
        return str(self.value)
