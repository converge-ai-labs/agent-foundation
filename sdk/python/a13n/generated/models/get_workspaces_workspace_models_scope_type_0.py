from enum import StrEnum


class GetWorkspacesWorkspaceModelsScopeType0(StrEnum):
    ORGANIZATION = "organization"
    WORKSPACE = "workspace"

    def __str__(self) -> str:
        return str(self.value)
