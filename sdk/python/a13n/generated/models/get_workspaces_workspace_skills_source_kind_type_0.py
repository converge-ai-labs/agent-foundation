from enum import StrEnum


class GetWorkspacesWorkspaceSkillsSourceKindType0(StrEnum):
    GITHUB = "github"
    ZIP = "zip"

    def __str__(self) -> str:
        return str(self.value)
