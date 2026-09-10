from enum import StrEnum


class AgentConfigInputSubagentMode(StrEnum):
    ASYNC = "async"
    INLINE = "inline"

    def __str__(self) -> str:
        return str(self.value)
