"""Composio app metadata and hosted OAuth configuration selection."""

from __future__ import annotations

import re
from dataclasses import dataclass

from jsonschema import Draft202012Validator
from pydantic import JsonValue

from a13n_service.connectivity.domain import JsonObject

from ...contracts import BeforeDispatch, ConnectorProviderError, DiscoveredConnector
from ...discovery import is_credential_field
from ...http import ConnectorHttpClient
from ...validation import optional_string, path_segment, required_object, required_string
from ..discovery import DirectoryBudget, directory_items
from .configuration import COMPOSIO_ENDPOINT

TOOLKIT_VERSION = re.compile(r"^[0-9]{8}_[0-9]{2}$")
MANAGED = "managed"


@dataclass(frozen=True, slots=True)
class AuthConfiguration:
    id: str
    name: str
    toolkit: str
    managed: bool


class ComposioCatalog:
    def __init__(self, http: ConnectorHttpClient, api_key: str) -> None:
        self._http = http
        self._api_key = api_key

    async def configurations(self) -> tuple[AuthConfiguration, ...]:
        items = await directory_items(
            self._http,
            endpoint=COMPOSIO_ENDPOINT,
            api_key=self._api_key,
            path="/api/v3.1/auth_configs",
            budget=DirectoryBudget(),
            page_size=50,
        )
        return tuple(
            AuthConfiguration(
                id=required_string(item, "id", max_length=256),
                name=optional_string(item.get("name"), max_length=256) or required_string(item, "id", max_length=256),
                toolkit=required_string(required_object(item.get("toolkit")), "slug", max_length=128),
                managed=item.get("is_composio_managed") is True,
            )
            for item in items
            if item.get("status") == "ENABLED" and item.get("auth_scheme") == "OAUTH2"
        )

    async def directory(self) -> tuple[DiscoveredConnector, ...]:
        toolkits = await directory_items(
            self._http,
            endpoint=COMPOSIO_ENDPOINT,
            api_key=self._api_key,
            path="/api/v3.1/toolkits",
            budget=DirectoryBudget(),
        )
        configurations = await self.configurations()
        return tuple(connector_metadata(item, configurations) for item in toolkits)

    async def connector(self, key: str) -> DiscoveredConnector:
        item = required_object(
            await self._http.request(
                "GET",
                endpoint=COMPOSIO_ENDPOINT,
                path=f"/api/v3.1/toolkits/{path_segment(key)}",
                api_key=self._api_key,
            )
        )
        if required_string(item, "slug", max_length=128) != key:
            raise ConnectorProviderError("provider_mismatch")
        connector = connector_metadata(item, await self.configurations())
        if connector.unavailable_reason:
            return connector
        fields, reason = connection_fields(item)
        schema = dict(connector.setup_schema)
        if fields is not None:
            schema["properties"] = {**required_object(schema["properties"]), "connection_data": fields}
            if fields.get("required"):
                schema["required"] = ["toolkit_version", "auth_config_id", "connection_data"]
        return connector.model_copy(update={"setup_schema": schema, "unavailable_reason": reason})

    async def resolve_auth_config(
        self,
        key: str,
        selection: str,
        before_shared_setup: BeforeDispatch | None,
    ) -> str:
        configurations = [item for item in await self.configurations() if item.toolkit == key]
        if selection != MANAGED:
            if any(item.id == selection for item in configurations):
                return selection
            raise ConnectorProviderError("auth_configuration_unavailable")
        managed = sorted(item.id for item in configurations if item.managed)
        if managed:
            return managed[0]
        if before_shared_setup is None:
            raise ConnectorProviderError("shared_setup_unavailable")
        await before_shared_setup()
        value = required_object(
            await self._http.request(
                "POST",
                endpoint=COMPOSIO_ENDPOINT,
                path="/api/v3.1/auth_configs",
                api_key=self._api_key,
                json_body={
                    "toolkit": {"slug": key},
                    "auth_config": {
                        "type": "use_composio_managed_auth",
                        "name": f"Agent Foundation · {key}",
                    },
                },
                write=True,
            )
        )
        try:
            if required_string(required_object(value.get("toolkit")), "slug") != key:
                raise ValueError("wrong toolkit")
            auth = required_object(value.get("auth_config"))
            if auth.get("is_composio_managed") is not True or auth.get("auth_scheme") != "OAUTH2":
                raise ValueError("wrong managed configuration")
            return required_string(auth, "id", max_length=256)
        except ValueError as error:
            raise ConnectorProviderError("invalid_provider_response", outcome_unknown=True) from error


def connector_metadata(item: JsonObject, configurations: tuple[AuthConfiguration, ...]) -> DiscoveredConnector:
    key = required_string(item, "slug", max_length=128)
    meta = required_object(item.get("meta"))
    version = optional_string(meta.get("version"), max_length=128)
    managed = "OAUTH2" in _auth_schemes(item, "composio_managed_auth_schemes")
    choices: list[JsonValue] = [{"const": MANAGED, "title": "Managed OAuth (recommended)"}] if managed else []
    choices.extend({"const": config.id, "title": config.name} for config in configurations if config.toolkit == key)
    reason = None
    if item.get("no_auth") is True:
        reason = "This application needs no authentication; hosted account setup is unavailable."
    elif not choices:
        reason = (
            "Create an OAuth2 auth config in Composio Dashboard to connect this application."
            if "OAUTH2" in _auth_schemes(item, "auth_schemes")
            else "This application's authentication method is not supported by hosted OAuth setup."
        )
    elif version is None or TOOLKIT_VERSION.fullmatch(version) is None:
        reason = "This application does not expose a supported immutable toolkit version."
    schema: JsonObject = {"not": {}}
    if reason is None:
        schema = {
            "type": "object",
            "properties": {
                "auth_config_id": {
                    "type": "string",
                    "title": "OAuth configuration",
                    "oneOf": choices,
                    "default": MANAGED if managed else required_object(choices[0])["const"],
                },
                "toolkit_version": {"type": "string", "const": version},
                "connection_data": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            "required": ["toolkit_version"] if managed else ["toolkit_version", "auth_config_id"],
            "additionalProperties": False,
        }
    return DiscoveredConnector(
        key=key,
        name=required_string(item, "name", max_length=128),
        description=optional_string(meta.get("description"), max_length=16_384),
        logo_url=optional_string(meta.get("logo"), max_length=2048),
        unavailable_reason=reason,
        setup_schema=schema,
        authentication_methods=("OAUTH2",) if reason is None else (),
    )


def connection_fields(item: JsonObject) -> tuple[JsonObject | None, str | None]:
    details = item.get("auth_config_details")
    if not isinstance(details, list):
        raise ConnectorProviderError("invalid_provider_response")
    oauth = next(
        (required_object(detail) for detail in details if required_object(detail).get("mode") == "OAUTH2"), None
    )
    if oauth is None:
        return None, "This application does not publish hosted OAuth connection fields."
    fields = required_object(required_object(oauth.get("fields")).get("connected_account_initiation"))
    properties: JsonObject = {}
    required: list[JsonValue] = []
    for group in ("required", "optional"):
        entries = fields.get(group, [])
        if not isinstance(entries, list):
            raise ConnectorProviderError("invalid_provider_response")
        for entry in entries:
            field = required_object(entry)
            name = required_string(field, "name", max_length=128)
            kind = field.get("type")
            unsafe = (
                is_credential_field(name)
                or field.get("is_secret") is True
                or kind not in {"string", "number", "integer", "boolean"}
            )
            if unsafe:
                if group == "required":
                    return (
                        None,
                        "This application requires connection fields that must be configured in Composio's hosted flow.",
                    )
                continue
            definition: JsonObject = {"type": kind, "title": optional_string(field.get("displayName")) or name}
            description = optional_string(field.get("description"), max_length=16_384)
            if description:
                definition["description"] = description
            if "default" in field and Draft202012Validator(definition).is_valid(field["default"]):
                definition["default"] = field["default"]
            properties[name] = definition
            if group == "required":
                required.append(name)
    if not properties:
        return None, None
    return {
        "type": "object",
        "title": "Connection details",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }, None


def _auth_schemes(item: JsonObject, field: str) -> tuple[str, ...]:
    values = item.get(field, [])
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ConnectorProviderError("invalid_provider_response")
    return tuple(value for value in values if isinstance(value, str))
