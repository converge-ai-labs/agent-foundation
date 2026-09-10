from enum import StrEnum


class ThreadRunSubmissionReceiptOutcome(StrEnum):
    QUEUED = "queued"
    RUN_ACCEPTED = "run_accepted"

    def __str__(self) -> str:
        return str(self.value)
