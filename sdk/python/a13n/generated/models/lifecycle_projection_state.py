from enum import StrEnum


class LifecycleProjectionState(StrEnum):
    ABANDONED = "abandoned"
    PENDING = "pending"
    PROJECTED = "projected"
    PROJECTING = "projecting"
    RETRY_WAIT = "retry_wait"

    def __str__(self) -> str:
        return str(self.value)
