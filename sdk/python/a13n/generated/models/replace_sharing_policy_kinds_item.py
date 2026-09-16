from enum import StrEnum


class ReplaceSharingPolicyKindsItem(StrEnum):
    DAILY = "daily"
    LONG_TERM = "long_term"

    def __str__(self) -> str:
        return str(self.value)
