from enum import StrEnum


class ConnectorSetupLaunchStatus(StrEnum):
    COMPLETED = "completed"
    EXPIRED = "expired"
    FAILED = "failed"
    PENDING = "pending"

    def __str__(self) -> str:
        return str(self.value)
