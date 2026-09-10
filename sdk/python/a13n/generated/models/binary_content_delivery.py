from enum import StrEnum


class BinaryContentDelivery(StrEnum):
    AUTO = "auto"
    ENVIRONMENT_PATH = "environment_path"
    MODEL_CONTENT = "model_content"
    MODEL_URL = "model_url"

    def __str__(self) -> str:
        return str(self.value)
