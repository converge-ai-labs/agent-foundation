from enum import StrEnum


class UpdateServiceAccountRequestRole(StrEnum):
    BUILDER = "builder"
    RUNNER = "runner"
    VIEWER = "viewer"

    def __str__(self) -> str:
        return str(self.value)
