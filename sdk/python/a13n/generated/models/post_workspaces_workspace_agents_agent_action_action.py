from enum import StrEnum


class PostWorkspacesWorkspaceAgentsAgentActionAction(StrEnum):
    ARCHIVE = "archive"
    DISABLE = "disable"
    ENABLE = "enable"
    UNARCHIVE = "unarchive"

    def __str__(self) -> str:
        return str(self.value)
