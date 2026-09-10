from enum import StrEnum


class TraceSummaryRunAttemptOutcomeType0(StrEnum):
    CANCELLED = "cancelled"
    FAILED = "failed"
    SUCCEEDED = "succeeded"
    YIELDED = "yielded"

    def __str__(self) -> str:
        return str(self.value)
