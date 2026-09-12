"""Deployment-resolved Remote MCP server catalog."""

from __future__ import annotations

from importlib.resources import files
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from a13n_service.application_errors import ErrorCategory
from a13n_service.collection_cursors import (
    CollectionCursorMismatchError,
    InvalidCollectionCursorError,
    decode_collection_cursor,
    encode_collection_cursor,
)
from a13n_service.configuration.sections import MCPServerSettings
from a13n_service.connectivity.browser_urls import split_browser_url
from a13n_service.endpoint_policy import EndpointPolicy, EndpointPolicyError

from .errors import MCPConnectionError


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MCPServer(_StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9-]{0,127}$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1024)
    endpoint_url: str = Field(min_length=1, max_length=2048)
    auth_mode: Literal["none", "bearer", "oauth", "static_headers"]
    documentation_url: str | None = Field(default=None, min_length=1, max_length=2048)
    logo_url: str | None = Field(default=None, min_length=1, max_length=2048)
    requirements: str = Field(default="", max_length=2048)
    static_header_names: tuple[Annotated[str, Field(min_length=1, max_length=128)], ...] = Field(
        default=(), max_length=16
    )
    origin: Literal["builtin", "deployment"] = "builtin"


class MCPServerCollection(_StrictModel):
    items: tuple[MCPServer, ...]
    next_cursor: str | None = None


_BUILTINS = TypeAdapter(tuple[MCPServer, ...]).validate_json(
    files("a13n_service.connectivity.mcp").joinpath("servers.json").read_text()
)


class MCPServerCatalog:
    def __init__(self, configured: tuple[MCPServerSettings, ...], endpoint_policy: EndpointPolicy) -> None:
        entries = {entry.key: entry for entry in _BUILTINS}
        configured_keys: set[str] = set()
        for setting in configured:
            if setting.key in configured_keys:
                raise ValueError(f"Duplicate deployment MCP server key: {setting.key}")
            configured_keys.add(setting.key)
            if setting.key in entries and not setting.override_builtin:
                raise ValueError(
                    f"Deployment MCP server {setting.key!r} collides with a builtin; set override_builtin = true"
                )
            entries[setting.key] = MCPServer.model_validate(
                {**setting.model_dump(exclude={"override_builtin"}), "origin": "deployment"}
            )
        self._items = tuple(sorted(entries.values(), key=lambda item: item.key))
        self._by_key = {item.key: item for item in self._items}
        for item in self._items:
            self._validate(item, endpoint_policy)

    @staticmethod
    def _validate(item: MCPServer, endpoint_policy: EndpointPolicy) -> None:
        try:
            endpoint_policy.validate_syntax(item.endpoint_url)
            for url in (item.documentation_url, item.logo_url):
                if url is not None:
                    parsed = urlsplit(url)
                    split_browser_url(parsed._replace(fragment="").geturl())
        except (EndpointPolicyError, ValueError) as error:
            raise ValueError(f"Invalid MCP server catalog entry: {item.key}") from error
        names = tuple(name.casefold() for name in item.static_header_names)
        if len(set(names)) != len(names) or ((item.auth_mode == "static_headers") != bool(names)):
            raise ValueError(f"Invalid MCP server static headers: {item.key}")

    def get(self, key: str) -> MCPServer:
        try:
            return self._by_key[key]
        except KeyError as error:
            raise MCPConnectionError(
                "resource_not_found", "The requested resource was not found.", category=ErrorCategory.not_found
            ) from error

    def documentation_urls(self) -> dict[str, str]:
        return {item.endpoint_url: item.documentation_url for item in self._items if item.documentation_url is not None}

    def list(self, *, query: str, limit: int, cursor: str | None) -> MCPServerCollection:
        normalized_query = query.casefold().strip()
        scope: dict[str, object] = {"query": normalized_query}
        after = ""
        if cursor is not None:
            try:
                payload = decode_collection_cursor(cursor, scope=scope, kind="mcp_servers")
                after_value = payload["key"]
                if not isinstance(after_value, str):
                    raise InvalidCollectionCursorError
                after = after_value
            except (CollectionCursorMismatchError, InvalidCollectionCursorError, KeyError) as error:
                raise MCPConnectionError(
                    "invalid_cursor", "Collection cursor is invalid.", category=ErrorCategory.invalid_request
                ) from error
        matching = [
            item
            for item in self._items
            if item.key > after
            and (
                not normalized_query
                or normalized_query in item.name.casefold()
                or normalized_query in item.description.casefold()
                or normalized_query in item.key
            )
        ]
        page = matching[:limit]
        next_cursor = (
            encode_collection_cursor({"key": page[-1].key}, scope=scope, kind="mcp_servers")
            if len(matching) > limit
            else None
        )
        return MCPServerCollection(items=tuple(page), next_cursor=next_cursor)
