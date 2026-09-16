from enum import StrEnum


class MessagingPolicyInteractionMode(StrEnum):
    CHAT = "chat"
    DISCUSSION = "discussion"
    MENTION = "mention"

    def __str__(self) -> str:
        return str(self.value)
