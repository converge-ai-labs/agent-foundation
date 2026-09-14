from enum import StrEnum


class AgentReviewerOnError(StrEnum):
    ALLOW = "allow"
    APPROVAL_REQUIRED = "approval_required"
    DENY = "deny"

    def __str__(self) -> str:
        return str(self.value)
