from enum import StrEnum


class DocumentAccessReasonKind(StrEnum):
    OWNER = "owner"
    POLICY = "policy"
    PUBLICATION = "publication"

    def __str__(self) -> str:
        return str(self.value)
