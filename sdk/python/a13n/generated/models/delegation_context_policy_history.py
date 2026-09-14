from enum import StrEnum


class DelegationContextPolicyHistory(StrEnum):
    NONE = "none"
    SELECTED = "selected"
    SUMMARY = "summary"

    def __str__(self) -> str:
        return str(self.value)
