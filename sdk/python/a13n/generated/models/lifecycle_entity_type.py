from enum import StrEnum


class LifecycleEntityType(StrEnum):
    RUN = "run"
    RUN_ATTEMPT = "run_attempt"

    def __str__(self) -> str:
        return str(self.value)
