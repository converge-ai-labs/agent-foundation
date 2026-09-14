from enum import StrEnum


class ToolRiskLevel(StrEnum):
    EXTRA_HIGH = "extra_high"
    HIGH = "high"
    LOW = "low"
    MEDIUM = "medium"

    def __str__(self) -> str:
        return str(self.value)
