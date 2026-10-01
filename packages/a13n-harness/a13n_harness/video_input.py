"""Structural native URL support and inline video input limits."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class VideoUrlType(StrEnum):
    YOUTUBE = "youtube"


class UrlInputSupport(BaseModel):
    """URL subtypes consumed natively by the selected transport."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    video: frozenset[VideoUrlType] = Field(default_factory=frozenset)

    @field_serializer("video", when_used="json")
    def _serialize_video(self, value: frozenset[VideoUrlType]) -> list[str]:
        return sorted(item.value for item in value)


class VideoInputPolicy(BaseModel):
    """Base64-after byte budget for both one video and all inline videos in a request."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    max_video_bytes: int = Field(
        default=10 * 1024 * 1024,
        gt=0,
        description="Maximum Base64-encoded bytes per video and in aggregate per model request.",
    )

    @property
    def max_raw_bytes(self) -> int:
        return (self.max_video_bytes // 4) * 3


def encoded_video_bytes(raw_bytes: int) -> int:
    """Calculate Base64 size without allocating an encoded copy."""
    return 4 * ((raw_bytes + 2) // 3)
