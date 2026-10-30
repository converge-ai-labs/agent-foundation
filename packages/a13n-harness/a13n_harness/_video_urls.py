"""Native YouTube admission shared with request projection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai import VideoUrl

from a13n_harness._urls import require_audience_safe_url
from a13n_harness.video_input import VideoUrlType

if TYPE_CHECKING:
    from a13n_harness.spec import HarnessModelCharacteristics


def video_url_error(video: VideoUrl, characteristics: HarnessModelCharacteristics | None) -> str | None:
    """Only explicitly supported YouTube URLs can reach native adapters."""
    try:
        require_audience_safe_url(video.url)
        if len(video.url) > 16 * 1024 or video.force_download is not False:
            return "video_url_invalid"
        if not video.is_youtube:
            return "video_url_requires_materialization"
        if not video.media_type.startswith("video/"):
            return "video_url_invalid"
    except (TypeError, ValueError):
        return "video_url_invalid"
    if characteristics is None or VideoUrlType.YOUTUBE not in characteristics.url_input.video:
        return "video_url_unsupported"
    return None
