from enum import StrEnum


class AuthorizationStatus(StrEnum):
    AWAITING_COMPLETION = "awaiting_completion"
    AWAITING_USER = "awaiting_user"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    EXPIRED = "expired"
    FAILED = "failed"
    PREPARING = "preparing"
    PROCESSING = "processing"

    def __str__(self) -> str:
        return str(self.value)
