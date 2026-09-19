"""Reusable typed Web definitions and bounded operation inputs."""

from .contracts import (
    WebPolicy,
    WebProviderError,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)
from .definition import WebProviderDefinition
from .errors import WebProviderResponseError
from .options import ScrapeOptions, SearchOptions
from .transport import WebProviderTransport

__all__ = [
    "ScrapeOptions",
    "SearchOptions",
    "WebPolicy",
    "WebProviderDefinition",
    "WebProviderError",
    "WebProviderResponseError",
    "WebProviderTransport",
    "WebScrapeRequest",
    "WebScrapeResult",
    "WebSearchRequest",
    "WebSearchResponse",
    "WebSearchResult",
]
