from enum import StrEnum


class ToolSetupDestinationOperationType0(StrEnum):
    SCRAPE = "scrape"
    SEARCH = "search"

    def __str__(self) -> str:
        return str(self.value)
