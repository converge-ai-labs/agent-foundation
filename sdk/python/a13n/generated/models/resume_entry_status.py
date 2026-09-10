from enum import StrEnum


class ResumeEntryStatus(StrEnum):
    CANCELLED = "cancelled"
    RESOLVED = "resolved"

    def __str__(self) -> str:
        return str(self.value)
