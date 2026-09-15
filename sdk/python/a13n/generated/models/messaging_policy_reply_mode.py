from enum import StrEnum


class MessagingPolicyReplyMode(StrEnum):
    AUTO = "auto"
    MAIN = "main"
    THREAD = "thread"

    def __str__(self) -> str:
        return str(self.value)
