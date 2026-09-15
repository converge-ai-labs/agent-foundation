from enum import StrEnum


class BotReplyObservationProviderKey(StrEnum):
    LARK = "lark"
    SLACK = "slack"

    def __str__(self) -> str:
        return str(self.value)
