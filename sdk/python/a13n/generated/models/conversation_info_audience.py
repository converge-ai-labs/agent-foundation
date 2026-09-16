from enum import StrEnum


class ConversationInfoAudience(StrEnum):
    DIRECT = "direct"
    PRIVATE = "private"
    PUBLIC = "public"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return str(self.value)
