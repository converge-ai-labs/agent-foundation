from enum import StrEnum


class ToolDefinitionSupportedPermissionsItem(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"
    INHERIT = "inherit"
    REVIEW = "review"

    def __str__(self) -> str:
        return str(self.value)
