from enum import StrEnum


class ModelCapability(StrEnum):
    AUDIO_UNDERSTANDING = "audio_understanding"
    IMAGE_UNDERSTANDING = "image_understanding"
    VIDEO_UNDERSTANDING = "video_understanding"

    def __str__(self) -> str:
        return str(self.value)
