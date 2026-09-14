from enum import StrEnum


class ToolDefinitionSupportedPermissionsItem(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    AUTO = "auto"
    DENY = "deny"
    REVIEW = "review"

    def __str__(self) -> str:
        return str(self.value)
