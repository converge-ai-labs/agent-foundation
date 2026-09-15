from enum import StrEnum


class ModelDeclarationsThinkingEffortsItem(StrEnum):
    HIGH = "high"
    LOW = "low"
    MEDIUM = "medium"
    MINIMAL = "minimal"
    XHIGH = "xhigh"

    def __str__(self) -> str:
        return str(self.value)
