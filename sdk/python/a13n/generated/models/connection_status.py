from enum import StrEnum


class ConnectionStatus(StrEnum):
    ACTION_REQUIRED = "action_required"
    DISABLED = "disabled"
    PENDING = "pending"
    READY = "ready"

    def __str__(self) -> str:
        return str(self.value)
