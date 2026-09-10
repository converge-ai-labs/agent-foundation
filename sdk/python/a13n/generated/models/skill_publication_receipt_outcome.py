from enum import StrEnum


class SkillPublicationReceiptOutcome(StrEnum):
    ALREADY_CURRENT = "already_current"
    PUBLISHED = "published"

    def __str__(self) -> str:
        return str(self.value)
