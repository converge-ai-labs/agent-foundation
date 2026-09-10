from enum import StrEnum


class NotificationSubscriptionTopicsItem(StrEnum):
    PENDING_ACTION_UPDATED = "pending_action.updated"
    RUN_UPDATED = "run.updated"
    SESSION_UPDATED = "session.updated"
    THREAD_UPDATED = "thread.updated"

    def __str__(self) -> str:
        return str(self.value)
