from enum import StrEnum


class ModelCandidateParameterSupportAdditionalProperty(StrEnum):
    SUPPORTED = "supported"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"

    def __str__(self) -> str:
        return str(self.value)
