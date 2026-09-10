from enum import StrEnum


class MCPAuthMode(StrEnum):
    BEARER = "bearer"
    NONE = "none"
    OAUTH = "oauth"
    STATIC_HEADERS = "static_headers"

    def __str__(self) -> str:
        return str(self.value)
