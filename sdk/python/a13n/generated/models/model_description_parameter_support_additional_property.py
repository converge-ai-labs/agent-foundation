from enum import StrEnum


class ModelDescriptionParameterSupportAdditionalProperty(StrEnum):
    SUPPORTED = "supported"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"

    def __str__(self) -> str:
        return str(self.value)
