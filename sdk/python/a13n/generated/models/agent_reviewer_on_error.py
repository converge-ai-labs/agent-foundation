from enum import StrEnum


class AgentReviewerOnError(StrEnum):
    APPROVAL_REQUIRED = "approval_required"
    DENY = "deny"

    def __str__(self) -> str:
        return str(self.value)
