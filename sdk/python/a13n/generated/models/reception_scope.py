from enum import StrEnum


class ReceptionScope(StrEnum):
    ALL_ACCESSIBLE = "all_accessible"
    CONFIGURED_TARGETS = "configured_targets"

    def __str__(self) -> str:
        return str(self.value)
