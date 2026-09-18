"""Bounded upstream directory reads and safe implementation-owned setup schemas."""

from __future__ import annotations

import json
from asyncio import get_running_loop, timeout_at
from dataclasses import dataclass, field

from a13n_harness.providers.connector.contracts import JsonObject

from .bounds import DISCOVERY_MAX_BYTES, DISCOVERY_MAX_PAGES, DISCOVERY_MAX_TOOLS
from .contracts import ConnectorProviderError
from .http import ConnectorHttpClient
from .validation import optional_string, required_object


@dataclass(slots=True)
class DirectoryBudget:
    deadline: float = field(default_factory=lambda: get_running_loop().time() + 30)
    pages: int = 0
    items: int = 0
    bytes: int = 0

    def record(self, value: JsonObject, item_count: int) -> None:
        self.pages += 1
        self.items += item_count
        self.bytes += len(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
        if self.pages > DISCOVERY_MAX_PAGES or self.items > DISCOVERY_MAX_TOOLS or self.bytes > DISCOVERY_MAX_BYTES:
            raise ConnectorProviderError("directory_too_large")


async def directory_items(
    http: ConnectorHttpClient,
    *,
    endpoint: str,
    api_key: str,
    path: str,
    budget: DirectoryBudget,
    page_size: int = 100,
) -> tuple[JsonObject, ...]:
    items: list[JsonObject] = []
    seen_cursors: set[str] = set()
    cursor: str | None = None
    for _ in range(DISCOVERY_MAX_PAGES):
        params = {"limit": str(page_size)}
        if cursor is not None:
            params["cursor"] = cursor
        try:
            async with timeout_at(budget.deadline):
                value = required_object(
                    await http.request("GET", endpoint=endpoint, path=path, api_key=api_key, params=params)
                )
        except TimeoutError as error:
            raise ConnectorProviderError("provider_timeout", retryable=True) from error
        page = value.get("items")
        if not isinstance(page, list) or len(page) > page_size:
            raise ConnectorProviderError("invalid_provider_response")
        budget.record(value, len(page))
        items.extend(required_object(item) for item in page)
        cursor = optional_string(value.get("next_cursor"))
        if cursor is None:
            total = value.get("total_items")
            if total is not None and (type(total) is not int or total != len(items)):
                raise ConnectorProviderError("incomplete_directory")
            return tuple(items)
        if not cursor or cursor in seen_cursors or not page:
            raise ConnectorProviderError("invalid_provider_response")
        seen_cursors.add(cursor)
    raise ConnectorProviderError("directory_too_large")


_CREDENTIAL_FIELDS = frozenset(
    {
        "credential",
        "credentials",
        "password",
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "cookie",
        "client_secret",
    }
)


def is_credential_field(name: str) -> bool:
    return name.casefold() in _CREDENTIAL_FIELDS
