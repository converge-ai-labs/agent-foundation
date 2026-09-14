from enum import StrEnum


class ObservationStatusType0(StrEnum):
    ERROR = "error"
    OK = "ok"
    UNSET = "unset"

    def __str__(self) -> str:
        return str(self.value)
