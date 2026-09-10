from enum import StrEnum


class ConnectionCleanupReceiptRemoteStatus(StrEnum):
    FAILED = "failed"
    NOT_REQUIRED = "not_required"
    SUCCEEDED = "succeeded"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return str(self.value)
