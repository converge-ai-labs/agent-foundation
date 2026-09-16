from enum import StrEnum


class VerificationAcknowledgementOutcome(StrEnum):
    FAILED = "failed"
    UNVERIFIED = "unverified"

    def __str__(self) -> str:
        return str(self.value)
