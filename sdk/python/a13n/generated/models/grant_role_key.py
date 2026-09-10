from enum import StrEnum


class GrantRoleKey(StrEnum):
    ADMIN = "admin"
    BUILDER = "builder"
    MEMBER = "member"
    RUNNER = "runner"
    VIEWER = "viewer"

    def __str__(self) -> str:
        return str(self.value)
