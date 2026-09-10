from enum import StrEnum


class QueuedSubmissionState(StrEnum):
    CONSUMED = "consumed"
    FAILED = "failed"
    QUEUED = "queued"

    def __str__(self) -> str:
        return str(self.value)
