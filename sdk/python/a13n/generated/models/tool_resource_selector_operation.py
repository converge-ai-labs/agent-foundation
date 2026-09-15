from enum import StrEnum


class ToolResourceSelectorOperation(StrEnum):
    SCRAPE = "scrape"
    SEARCH = "search"

    def __str__(self) -> str:
        return str(self.value)
