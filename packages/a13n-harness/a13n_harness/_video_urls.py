"""Shared native video URL admission for tools and request projections."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai import VideoUrl

from a13n_harness._urls import require_audience_safe_url

if TYPE_CHECKING:
    from a13n_harness.spec import HarnessModelCharacteristics


def supports_video_urls(characteristics: HarnessModelCharacteristics | None) -> bool:
    from a13n_harness.spec import ModelCapability

    return characteristics is not None and bool(
        characteristics.capabilities
        & {ModelCapability.VIDEO_URL_UNDERSTANDING, ModelCapability.YOUTUBE_URL_UNDERSTANDING}
    )


def video_url_error(video: VideoUrl, characteristics: HarnessModelCharacteristics | None) -> str | None:
    """Admit only safe URLs of the declared kind, never an explicit download."""
    from a13n_harness.spec import ModelCapability

    try:
        require_audience_safe_url(video.url)
        if len(video.url) > 16 * 1024 or video.force_download is not False:
            return "video_url_invalid"
        if not video.media_type.startswith("video/"):
            return "video_url_invalid"
        required = (
            ModelCapability.YOUTUBE_URL_UNDERSTANDING if video.is_youtube else ModelCapability.VIDEO_URL_UNDERSTANDING
        )
    except (TypeError, ValueError):
        return "video_url_invalid"
    if characteristics is None or required not in characteristics.capabilities:
        return "video_url_unsupported"
    return None
