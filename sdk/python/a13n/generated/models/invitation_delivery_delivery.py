from enum import StrEnum


class InvitationDeliveryDelivery(StrEnum):
    FAILED = "failed"
    MANUAL = "manual"
    SENT = "sent"

    def __str__(self) -> str:
        return str(self.value)
