from enum import StrEnum


class ResourceLifecycleEventPageResourceType(StrEnum):
    RUN = "run"
    RUN_ATTEMPT = "run_attempt"

    def __str__(self) -> str:
        return str(self.value)
