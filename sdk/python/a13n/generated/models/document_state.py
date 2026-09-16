from enum import StrEnum


class DocumentState(StrEnum):
    ACTIVE = "active"
    DELETED = "deleted"
    DELETING = "deleting"
    PENDING = "pending"
    UNCONFIRMED = "unconfirmed"

    def __str__(self) -> str:
        return str(self.value)
