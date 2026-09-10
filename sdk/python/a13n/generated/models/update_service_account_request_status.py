from enum import StrEnum


class UpdateServiceAccountRequestStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"

    def __str__(self) -> str:
        return str(self.value)
