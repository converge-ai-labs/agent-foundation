"""Operation limits and domain restrictions shared by embedded and hosted Web."""

from pydantic import Field

from .domains import DomainRestrictions

MAX_SCRAPE_CONTENT_BYTES = 4 * 1024 * 1024


class SearchOptions(DomainRestrictions):
    max_results: int = Field(default=5, ge=1, le=100)


class ScrapeOptions(DomainRestrictions):
    max_content_bytes: int = Field(default=512 * 1024, ge=1, le=MAX_SCRAPE_CONTENT_BYTES)
