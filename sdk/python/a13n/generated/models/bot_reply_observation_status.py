from enum import StrEnum


class BotReplyObservationStatus(StrEnum):
    DISPATCHING = "dispatching"
    OUTCOME_UNKNOWN = "outcome_unknown"
    REJECTED = "rejected"
    SUCCEEDED = "succeeded"

    def __str__(self) -> str:
        return str(self.value)
