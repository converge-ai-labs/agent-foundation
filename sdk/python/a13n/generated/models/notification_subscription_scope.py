from enum import StrEnum


class NotificationSubscriptionScope(StrEnum):
    THREAD = "thread"
    WORKSPACE = "workspace"

    def __str__(self) -> str:
        return str(self.value)
