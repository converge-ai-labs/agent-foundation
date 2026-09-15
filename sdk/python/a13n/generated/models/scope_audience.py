from enum import StrEnum


class ScopeAudience(StrEnum):
    DIRECT = "direct"
    PRIVATE = "private"
    PUBLIC = "public"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return str(self.value)
