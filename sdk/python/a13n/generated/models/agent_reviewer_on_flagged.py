from enum import StrEnum


class AgentReviewerOnFlagged(StrEnum):
    APPROVAL_REQUIRED = "approval_required"
    DENY = "deny"

    def __str__(self) -> str:
        return str(self.value)
