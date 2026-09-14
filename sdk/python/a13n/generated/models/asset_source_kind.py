from enum import StrEnum


class AssetSourceKind(StrEnum):
    RUN_OUTPUT = "run_output"
    UPLOAD = "upload"

    def __str__(self) -> str:
        return str(self.value)
