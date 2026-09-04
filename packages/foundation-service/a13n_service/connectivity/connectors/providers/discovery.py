"""Bounded upstream directory reads and safe implementation-owned setup schemas."""

from __future__ import annotations

from asyncio import get_running_loop, timeout_at
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from jsonschema import Draft202012Validator
from pydantic import JsonValue

from a13n_service.connectivity.bounds import DISCOVERY_MAX_BYTES, DISCOVERY_MAX_PAGES, DISCOVERY_MAX_TOOLS
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.management import canonical_json

from ..contracts import ConnectorProviderError, DiscoveredConnector
from ..http import ConnectorHttpClient
from ..validation import optional_string, required_object


@dataclass(slots=True)
class DirectoryBudget:
    deadline: float = field(default_factory=lambda: get_running_loop().time() + 30)
    pages: int = 0
    items: int = 0
    bytes: int = 0

    def record(self, value: JsonObject, item_count: int) -> None:
        self.pages += 1
        self.items += item_count
        self.bytes += len(canonical_json(value).encode())
        if self.pages > DISCOVERY_MAX_PAGES or self.items > DISCOVERY_MAX_TOOLS or self.bytes > DISCOVERY_MAX_BYTES:
            raise ConnectorProviderError("directory_too_large")


async def directory_items(
    http: ConnectorHttpClient,
    *,
    endpoint: str,
    api_key: str,
    path: str,
    pagination: Literal["next_cursor", "nextCursor", "offset"],
    budget: DirectoryBudget,
    page_size: int = 100,
) -> tuple[JsonObject, ...]:
    items: list[JsonObject] = []
    seen_cursors: set[str] = set()
    cursor: str | None = None
    expected_total: int | None = None
    for _ in range(DISCOVERY_MAX_PAGES):
        params = {"limit": str(page_size)}
        if pagination == "offset":
            params["offset"] = str(len(items))
        elif cursor is not None:
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
        if pagination == "offset":
            total = value.get("total")
            if type(total) is not int or not len(items) <= total <= DISCOVERY_MAX_TOOLS:
                raise ConnectorProviderError("invalid_provider_response")
            if expected_total is not None and expected_total != total:
                raise ConnectorProviderError("directory_changed")
            expected_total = total
            if len(items) == total:
                return tuple(items)
            if not page:
                raise ConnectorProviderError("invalid_provider_response")
        else:
            cursor = optional_string(value.get(pagination))
            if cursor is None:
                total = value.get("total_items" if pagination == "next_cursor" else "total")
                if total is not None and (type(total) is not int or total != len(items)):
                    raise ConnectorProviderError("incomplete_directory")
                return tuple(items)
            if not cursor or cursor in seen_cursors or not page:
                raise ConnectorProviderError("invalid_provider_response")
            seen_cursors.add(cursor)
    raise ConnectorProviderError("directory_too_large")


def setup_schema(auth_config_ids: list[str], *, toolkit_version: str | None = None) -> JsonObject:
    ids: list[JsonValue] = [item for item in sorted(set(auth_config_ids))]
    properties: JsonObject = {"auth_config_id": {"type": "string", "enum": ids}}
    required: list[JsonValue] = ["auth_config_id"]
    if toolkit_version is not None:
        properties["toolkit_version"] = {"type": "string", "const": toolkit_version}
        required.append("toolkit_version")
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


async def validate_discovered_setup(
    discover: Callable[[], Awaitable[tuple[DiscoveredConnector, ...]]], connector_key: str, setup: JsonObject
) -> None:
    connector = next((item for item in await discover() if item.key == connector_key), None)
    if connector is None or not connector.authentication_methods:
        raise ConnectorProviderError("connector_setup_unavailable")
    if not Draft202012Validator(connector.setup_schema).is_valid(setup):
        raise ConnectorProviderError("invalid_setup_options")
