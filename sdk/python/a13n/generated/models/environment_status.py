from enum import StrEnum


class EnvironmentStatus(StrEnum):
    DELETED = "deleted"
    RUNNING = "running"
    STOPPED = "stopped"
    UNAVAILABLE = "unavailable"
    UNPREPARED = "unprepared"

    def __str__(self) -> str:
        return str(self.value)
