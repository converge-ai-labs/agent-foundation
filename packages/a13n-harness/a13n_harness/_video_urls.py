"""Shared video URL admission and bounded acquisition."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urljoin

import httpx2
from pydantic_ai import BinaryContent, VideoUrl

from a13n_harness._urls import require_audience_safe_url
from a13n_harness.http import ProviderHttpError, bounded_response_body, outbound_tls_verify
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.video_input import VideoInputPolicy, VideoUrlType

if TYPE_CHECKING:
    from a13n_harness.spec import HarnessModelCharacteristics


def supports_video_urls(characteristics: HarnessModelCharacteristics | None) -> bool:
    from a13n_harness.spec import ModelCapability

    return characteristics is not None and (
        ModelCapability.VIDEO_UNDERSTANDING in characteristics.capabilities or bool(characteristics.url_input.video)
    )


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


async def download_video(
    url: str, *, media_type: str | None, policy: EndpointPolicy, limits: VideoInputPolicy
) -> BinaryContent:
    """Materialize a direct resource with bounded bytes and validated redirect hops."""
    require_audience_safe_url(url)
    endpoint = await policy.validate(url)
    async with httpx2.AsyncClient(verify=outbound_tls_verify(), timeout=30.0, follow_redirects=False) as client:
        for hop in range(6):
            async with client.stream("GET", endpoint) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if location is None or hop == 5:
                        raise ProviderHttpError("video_redirect_invalid")
                    target = urljoin(endpoint, location)
                    require_audience_safe_url(target)
                    # Never turn a direct resource into a downloaded YouTube page.
                    if VideoUrl(target).is_youtube:
                        raise ProviderHttpError("video_redirect_invalid")
                    endpoint, _ = await policy.validate_redirect(endpoint, target)
                    continue
                response.raise_for_status()
                declared = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if declared and declared != "application/octet-stream":
                    resolved = declared
                else:
                    try:
                        resolved = media_type or VideoUrl(endpoint).media_type
                    except ValueError:
                        resolved = ""
                if not resolved.startswith("video/") or (
                    media_type is not None and not media_type.startswith("video/")
                ):
                    raise ProviderHttpError("video_mime_invalid")
                data = await bounded_response_body(response, max_bytes=limits.max_raw_bytes)
                if not data:
                    raise ProviderHttpError("video_empty")
                return BinaryContent(data=data, media_type=resolved)
    raise ProviderHttpError("video_redirect_invalid")
