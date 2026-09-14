from enum import StrEnum


class ToolsetDefinitionKey(StrEnum):
    ASSETS = "assets"
    FILES = "files"
    SHELL = "shell"
    WEB = "web"

    def __str__(self) -> str:
        return str(self.value)
