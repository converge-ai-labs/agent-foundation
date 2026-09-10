from enum import StrEnum


class SafeFailureRetryHint(StrEnum):
    DEPENDENCY_CHANGE = "dependency_change"
    NEW_RUN = "new_run"
    NONE = "none"

    def __str__(self) -> str:
        return str(self.value)
