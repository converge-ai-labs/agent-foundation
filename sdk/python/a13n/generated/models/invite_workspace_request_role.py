from enum import StrEnum


class InviteWorkspaceRequestRole(StrEnum):
    ADMIN = "admin"
    BUILDER = "builder"
    RUNNER = "runner"
    VIEWER = "viewer"

    def __str__(self) -> str:
        return str(self.value)
