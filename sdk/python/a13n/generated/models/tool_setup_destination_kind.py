from enum import StrEnum


class ToolSetupDestinationKind(StrEnum):
    REVIEWER = "reviewer"
    WEB_PROVIDER = "web_provider"

    def __str__(self) -> str:
        return str(self.value)
