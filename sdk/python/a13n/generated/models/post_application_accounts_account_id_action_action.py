from enum import StrEnum


class PostApplicationAccountsAccountIdActionAction(StrEnum):
    DISABLE = "disable"
    ENABLE = "enable"

    def __str__(self) -> str:
        return str(self.value)
