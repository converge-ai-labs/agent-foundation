"""Shared Google SDK connection options for Gemini and Vertex."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx2

from .types import ModelConnection


def http_options(provider: ModelConnection, http_client: httpx2.AsyncClient) -> HttpOptions:
    from google.genai.types import HttpOptions, HttpRetryOptions

    return HttpOptions(
        base_url=provider.endpoint,
        headers=provider.extra_headers,
        httpx_async_client=http_client,
        timeout=int((http_client.timeout.read or 600) * 1000),
        retry_options=HttpRetryOptions(attempts=1),
    )


if TYPE_CHECKING:
    from google.genai.types import HttpOptions
