"""Shared image-input policy for model characteristics and request preparation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ImageInputPolicy(BaseModel):
    """Preparation limits for one model's image input, not native ModelSettings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    split_large_images: bool = True
    image_split_max_height: int = Field(default=4096, gt=0)
    image_split_overlap: int = Field(default=50, ge=0)
    max_image_bytes: int = Field(
        default=5 * 1024 * 1024,
        ge=0,
        description="Maximum base64-encoded bytes per image; zero disables this byte limit.",
    )
    max_image_dimension: int = Field(default=8000, ge=0, description="Maximum image axis; zero disables this limit.")
    max_images: int = Field(default=20, ge=0, description="Keep the newest images; zero removes all image input.")
    support_gif: bool = True

    @model_validator(mode="after")
    def _validate_overlap(self) -> ImageInputPolicy:
        if self.image_split_overlap >= self.image_split_max_height:
            raise ValueError("image split overlap must be smaller than the segment height")
        return self
