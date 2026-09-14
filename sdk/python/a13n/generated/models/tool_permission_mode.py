from enum import StrEnum


class ToolPermissionMode(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"
    REVIEW = "review"

    def __str__(self) -> str:
        return str(self.value)
