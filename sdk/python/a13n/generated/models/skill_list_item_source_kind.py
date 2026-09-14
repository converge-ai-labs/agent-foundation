from enum import StrEnum


class SkillListItemSourceKind(StrEnum):
    GITHUB = "github"
    ZIP = "zip"

    def __str__(self) -> str:
        return str(self.value)
