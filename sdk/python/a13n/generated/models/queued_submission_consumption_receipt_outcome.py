from enum import StrEnum


class QueuedSubmissionConsumptionReceiptOutcome(StrEnum):
    RUN_ACCEPTED = "run_accepted"
    SUBMISSION_FAILED = "submission_failed"

    def __str__(self) -> str:
        return str(self.value)
