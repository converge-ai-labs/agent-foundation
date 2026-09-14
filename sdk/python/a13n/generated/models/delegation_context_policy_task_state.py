from enum import StrEnum


class DelegationContextPolicyTaskState(StrEnum):
    ISOLATED = "isolated"
    SHARED = "shared"

    def __str__(self) -> str:
        return str(self.value)
