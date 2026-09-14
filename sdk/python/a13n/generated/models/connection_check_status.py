from enum import StrEnum


class ConnectionCheckStatus(StrEnum):
    ACTION_REQUIRED = "action_required"
    PASSED = "passed"
    UNAVAILABLE = "unavailable"

    def __str__(self) -> str:
        return str(self.value)
