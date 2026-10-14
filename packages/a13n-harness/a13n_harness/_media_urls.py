"""Bounded HTTP acquisition of direct image, video, and audio resources."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import cast
from urllib.parse import urljoin

import httpx2
from pydantic_ai import AudioUrl, BinaryContent, ImageUrl, VideoUrl

from a13n_harness._urls import require_audience_safe_url
from a13n_harness.http import ProviderHttpError, bounded_response_body, outbound_tls_verify
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.toolsets.file_media import NativeInputMediaKind


def media_kind(media_type: str) -> NativeInputMediaKind:
    kind, separator, subtype = media_type.partition("/")
    if kind not in {"image", "video", "audio"} or not separator or not subtype.strip():
        raise ProviderHttpError("media_mime_invalid")
    return cast(NativeInputMediaKind, kind)


def _infer_media_type(url: str) -> str:
    for url_type in (ImageUrl, VideoUrl, AudioUrl):
        try:
            return url_type(url).media_type
        except ValueError:
            continue
    raise ProviderHttpError("media_mime_invalid")


async def download_media(
    url: str,
    *,
    media_type: str | None,
    policy: EndpointPolicy,
    byte_limit: Callable[[NativeInputMediaKind], int],
) -> BinaryContent:
    """Authorize every hop, then bound the body for its resolved media kind."""
    require_audience_safe_url(url)
    if media_type is not None:
        media_kind(media_type)
    async with (
        asyncio.timeout(120.0),
        httpx2.AsyncClient(verify=outbound_tls_verify(), timeout=30.0, follow_redirects=False) as client,
    ):
        endpoint = await policy.validate(url)
        for hop in range(6):
            async with client.stream("GET", endpoint) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if location is None or hop == 5:
                        raise ProviderHttpError("media_redirect_invalid")
                    target = urljoin(endpoint, location)
                    require_audience_safe_url(target)
                    if VideoUrl(target).is_youtube:
                        raise ProviderHttpError("media_redirect_invalid")
                    endpoint, _ = await policy.validate_redirect(endpoint, target)
                    continue
                response.raise_for_status()
                declared = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                resolved = (
                    declared
                    if declared and declared != "application/octet-stream"
                    else media_type or _infer_media_type(endpoint)
                )
                kind = media_kind(resolved)
                data = await bounded_response_body(response, max_bytes=byte_limit(kind))
                if not data:
                    raise ProviderHttpError("media_empty")
                return BinaryContent(data=data, media_type=resolved)
    raise ProviderHttpError("media_redirect_invalid")
