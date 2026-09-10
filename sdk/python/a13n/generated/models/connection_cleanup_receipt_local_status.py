from enum import StrEnum


class ConnectionCleanupReceiptLocalStatus(StrEnum):
    DELETED = "deleted"
    DISABLED = "disabled"

    def __str__(self) -> str:
        return str(self.value)
