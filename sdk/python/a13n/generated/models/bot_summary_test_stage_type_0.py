from enum import StrEnum


class BotSummaryTestStageType0(StrEnum):
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    RECEIVED = "received"
    REJECTED = "rejected"
    REPLY_CONFIRMED = "reply_confirmed"
    STALE = "stale"
    WAITING = "waiting"

    def __str__(self) -> str:
        return str(self.value)
