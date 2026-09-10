from enum import StrEnum


class TraceSummaryTraceStatus(StrEnum):
    ERROR = "error"
    OK = "ok"
    UNSET = "unset"

    def __str__(self) -> str:
        return str(self.value)
