from enum import StrEnum


class ModelProfileInputModalitiesType0Item(StrEnum):
    AUDIO = "audio"
    IMAGE = "image"
    TEXT = "text"
    VIDEO = "video"

    def __str__(self) -> str:
        return str(self.value)
