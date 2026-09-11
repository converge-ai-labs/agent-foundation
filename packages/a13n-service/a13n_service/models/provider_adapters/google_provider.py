"""Shared Google SDK connection options for Gemini and Vertex."""

import httpx2
from google.genai.types import HttpOptions, HttpRetryOptions

from .types import RuntimeProvider


def http_options(provider: RuntimeProvider, http_client: httpx2.AsyncClient) -> HttpOptions:
    return HttpOptions(
        base_url=provider.endpoint,
        headers=provider.extra_headers,
        httpx_async_client=http_client,
        timeout=int((http_client.timeout.read or 600) * 1000),
        retry_options=HttpRetryOptions(attempts=1),
    )
