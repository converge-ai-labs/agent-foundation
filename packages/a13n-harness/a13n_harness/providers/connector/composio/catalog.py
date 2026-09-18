"""Composio app metadata and hosted authentication configuration selection."""

from __future__ import annotations

import re
from dataclasses import dataclass

from jsonschema import Draft202012Validator
from pydantic import JsonValue

from a13n_harness.providers.connector.contracts import JsonObject

from ..contracts import BeforeSharedSetup, ConnectorProviderError, DiscoveredConnector
from ..directory import DirectoryBudget, directory_items, is_credential_field
from ..http import ConnectorHttpClient
from ..validation import optional_string, path_segment, required_object, required_string
from .configuration import COMPOSIO_ENDPOINT, ComposioSetup

TOOLKIT_VERSION = re.compile(r"^[0-9]{8}_[0-9]{2}$")
SUPPORTED_SCHEMES = ("OAUTH2", "API_KEY", "BEARER_TOKEN", "BASIC")
AUTH_LABELS = {
    "OAUTH2": "OAuth 2.0",
    "API_KEY": "API key",
    "BEARER_TOKEN": "Bearer token",
    "BASIC": "Basic authentication",
}


@dataclass(frozen=True, slots=True)
class AuthConfiguration:
    id: str
    name: str
    toolkit: str
    managed: bool
    scheme: str
    scopes: str | None = None


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
                scheme=required_string(item, "auth_scheme", max_length=64),
                scopes=_scopes(item),
            )
            for item in items
            if item.get("status") == "ENABLED" and item.get("auth_scheme") in SUPPORTED_SCHEMES
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
        return connector_metadata(await self._toolkit(key), await self.configurations())

    async def prepare_setup(
        self, key: str, setup: JsonObject, before_shared_setup: BeforeSharedSetup | None
    ) -> AuthConfiguration:
        configured = ComposioSetup.model_validate(setup)
        item = await self._toolkit(key)
        configurations = await self.configurations()
        connector = connector_metadata(item, configurations)
        if connector.unavailable_reason:
            raise ConnectorProviderError("connector_setup_unavailable")
        if not Draft202012Validator(connector.setup_schema).is_valid(setup):
            raise ConnectorProviderError("invalid_setup_options")
        return await self.resolve_auth_config(
            key, configured.auth_config_id, before_shared_setup, configurations=configurations
        )

    async def prepare_credentials(
        self, key: str, setup: JsonObject, credentials: JsonObject, before_shared_setup: BeforeSharedSetup | None
    ) -> AuthConfiguration:
        configured = ComposioSetup.model_validate(setup)
        item = await self._toolkit(key)
        configurations = await self.configurations()
        connector = connector_metadata(item, configurations)
        if connector.unavailable_reason or not Draft202012Validator(connector.setup_schema).is_valid(setup):
            raise ConnectorProviderError("invalid_setup_options")
        selected = next(
            (config for config in configurations if config.id == configured.auth_config_id and config.toolkit == key),
            None,
        )
        scheme = selected.scheme if selected is not None else configured.auth_config_id.removeprefix("create:")
        if scheme not in {"API_KEY", "BEARER_TOKEN", "BASIC"}:
            raise ConnectorProviderError("invalid_setup_options")
        schema = _credential_schema(item, scheme)
        if not schema["properties"] or not Draft202012Validator(schema).is_valid(credentials):
            raise ConnectorProviderError("invalid_credentials")
        return await self.resolve_auth_config(
            key, configured.auth_config_id, before_shared_setup, configurations=configurations
        )

    async def _toolkit(self, key: str) -> JsonObject:
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
        return item

    async def resolve_auth_config(
        self,
        key: str,
        selection: str,
        before_shared_setup: BeforeSharedSetup | None,
        *,
        configurations: tuple[AuthConfiguration, ...],
    ) -> AuthConfiguration:
        configurations = tuple(item for item in configurations if item.toolkit == key)
        if not selection.startswith("create:"):
            selected = next((item for item in configurations if item.id == selection), None)
            if selected is not None:
                return selected
            raise ConnectorProviderError("auth_configuration_unavailable")
        scheme = selection.removeprefix("create:")
        if scheme not in SUPPORTED_SCHEMES:
            raise ConnectorProviderError("invalid_setup_options")
        matching = [item for item in configurations if item.scheme == scheme and (item.managed or scheme != "OAUTH2")]
        if len(matching) == 1:
            return matching[0]
        if matching:
            raise ConnectorProviderError("auth_configuration_ambiguous")
        if before_shared_setup is not None:
            await before_shared_setup(scheme)
        value = await self._http.request(
            "POST",
            endpoint=COMPOSIO_ENDPOINT,
            path="/api/v3.1/auth_configs",
            api_key=self._api_key,
            json_body={
                "toolkit": {"slug": key},
                "auth_config": {
                    "name": f"Agent Foundation · {key} · {AUTH_LABELS[scheme]}",
                    **(
                        {"type": "use_composio_managed_auth"}
                        if scheme == "OAUTH2"
                        else {
                            "type": "use_custom_auth",
                            "authScheme": scheme,
                            "credentials": {},
                        }
                    ),
                },
            },
            write=True,
        )
        try:
            response = required_object(value)
            if required_string(required_object(response.get("toolkit")), "slug") != key:
                raise ValueError("wrong toolkit")
            auth = required_object(response.get("auth_config"))
            if auth.get("auth_scheme") != scheme or (
                scheme == "OAUTH2" and auth.get("is_composio_managed") is not True
            ):
                raise ValueError("wrong authentication configuration")
            return AuthConfiguration(
                id=required_string(auth, "id", max_length=256),
                name=f"Agent Foundation · {key}",
                toolkit=key,
                managed=scheme == "OAUTH2",
                scheme=scheme,
            )
        except ValueError as error:
            raise ConnectorProviderError("invalid_provider_response", outcome_unknown=True) from error


def connector_metadata(item: JsonObject, configurations: tuple[AuthConfiguration, ...]) -> DiscoveredConnector:
    key = required_string(item, "slug", max_length=128)
    meta = required_object(item.get("meta"))
    version = optional_string(meta.get("version"), max_length=128)
    methods = tuple(scheme for scheme in SUPPORTED_SCHEMES if scheme in _auth_schemes(item, "auth_schemes"))
    configs = [config for config in configurations if config.toolkit == key]
    choices: list[JsonValue] = []
    for config in configs:
        label = f"{config.name} · {AUTH_LABELS[config.scheme]}"
        if config.scheme == "OAUTH2":
            label += " · Composio managed" if config.managed else " · Custom app"
        if config.scopes:
            label += f" · Scopes: {config.scopes}"
        choices.append({"const": config.id, "title": label})
    for scheme in methods:
        if any(config.scheme == scheme and (config.managed or scheme != "OAUTH2") for config in configs):
            continue
        if scheme == "OAUTH2":
            if scheme in _auth_schemes(item, "composio_managed_auth_schemes"):
                choices.append({"const": "create:OAUTH2", "title": "Composio managed OAuth 2.0"})
        elif not _requires_app_credentials(item, scheme):
            choices.append({"const": f"create:{scheme}", "title": AUTH_LABELS[scheme]})
    reason = None
    if item.get("no_auth") is True:
        reason = "This application needs no authentication; hosted account setup is unavailable."
    elif not choices:
        reason = (
            "Create an auth config in Composio Dashboard, then refresh applications."
            if methods
            else "This application's authentication method is not supported by hosted setup."
        )
    elif version is None or TOOLKIT_VERSION.fullmatch(version) is None:
        reason = "This application does not expose a supported immutable toolkit version."
    schema: JsonObject = {"not": {}}
    if reason is None:
        selection: JsonObject = {
            "type": "string",
            "title": "Authentication configuration",
            "oneOf": choices,
            "description": "Choose the application and permissions to use. Account credentials and instance details are entered securely on Composio.",
        }
        if len(choices) == 1:
            selection["default"] = required_object(choices[0])["const"]
        prefill_constraints: list[JsonValue] = []
        offered = [required_object(choice)["const"] for choice in choices]
        for scheme in SUPPORTED_SCHEMES:
            selections: list[JsonValue] = [config.id for config in configs if config.scheme == scheme]
            if f"create:{scheme}" in offered:
                selections.append(f"create:{scheme}")
            if selections:
                prefill_constraints.append(
                    {
                        "if": {"properties": {"auth_config_id": {"enum": selections}}},
                        "then": {"properties": {"connection_data": _connection_data_schema(item, scheme)}},
                    }
                )
        schema = {
            "type": "object",
            "properties": {
                "auth_config_id": selection,
                "toolkit_version": {"type": "string", "const": version},
                "connection_data": {"type": "object", "properties": {}},
            },
            "required": ["toolkit_version", "auth_config_id"],
            "additionalProperties": False,
            "allOf": prefill_constraints,
        }
    return DiscoveredConnector(
        key=key,
        name=required_string(item, "name", max_length=128),
        description=optional_string(meta.get("description"), max_length=16_384),
        logo_url=optional_string(meta.get("logo"), max_length=2048),
        unavailable_reason=reason,
        setup_schema=schema,
        authentication_methods=methods if item.get("no_auth") is not True else (),
        credential_schemas={scheme: _credential_schema(item, scheme) for scheme in methods if scheme != "OAUTH2"},
    )


def _requires_app_credentials(item: JsonObject, scheme: str) -> bool:
    for detail in _auth_details(item):
        if detail.get("mode") == scheme:
            fields = required_object(detail.get("fields"))
            creation = required_object(fields.get("auth_config_creation", {}))
            return bool(creation.get("required"))
    return False


def _scopes(item: JsonObject) -> str | None:
    # Only the documented non-secret scope field may leave the provider boundary.
    credentials = item.get("credentials")
    if not isinstance(credentials, dict):
        return None
    scopes = credentials.get("scopes")
    if isinstance(scopes, list) and all(isinstance(scope, str) for scope in scopes):
        scopes = ", ".join(scope for scope in scopes if isinstance(scope, str))
    return optional_string(scopes, max_length=2048)


def _auth_schemes(item: JsonObject, field: str) -> tuple[str, ...]:
    values = item.get(field)
    if values is None and field == "auth_schemes":
        values = [detail.get("mode") for detail in _auth_details(item)]
    if values is None:
        return ()
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ConnectorProviderError("invalid_provider_response")
    return tuple(value for value in values if isinstance(value, str))


def _auth_details(item: JsonObject) -> tuple[JsonObject, ...]:
    details = item.get("auth_config_details")
    if details is None:
        return ()
    if not isinstance(details, list):
        raise ConnectorProviderError("invalid_provider_response")
    return tuple(required_object(detail) for detail in details)


def _connection_data_schema(item: JsonObject, scheme: str) -> JsonObject:
    properties: JsonObject = {}
    for detail in _auth_details(item):
        if detail.get("mode") != scheme:
            continue
        fields = required_object(required_object(detail.get("fields")).get("connected_account_initiation", {}))
        for group in ("required", "optional"):
            entries = fields.get(group, [])
            if not isinstance(entries, list):
                raise ConnectorProviderError("invalid_provider_response")
            for entry in entries:
                field = required_object(entry)
                name = required_string(field, "name", max_length=128)
                kind = field.get("type")
                if (
                    is_credential_field(name)
                    or field.get("is_secret") is True
                    or kind not in {"string", "number", "integer", "boolean"}
                ):
                    continue
                properties[name] = {"type": kind}
    return {
        "type": "object",
        "description": "Optional non-secret defaults for the selected hosted authentication form.",
        "properties": properties,
        "additionalProperties": False,
    }


def _credential_schema(item: JsonObject, scheme: str) -> JsonObject:
    properties: JsonObject = {}
    required: list[JsonValue] = []
    for detail in _auth_details(item):
        if detail.get("mode") != scheme:
            continue
        fields = required_object(required_object(detail.get("fields")).get("connected_account_initiation", {}))
        for group in ("required", "optional"):
            entries = fields.get(group, [])
            if not isinstance(entries, list):
                raise ConnectorProviderError("invalid_provider_response")
            for entry in entries:
                field = required_object(entry)
                name = required_string(field, "name", max_length=128)
                if not is_credential_field(name) and field.get("is_secret") is not True:
                    continue
                properties[name] = {"type": "string", "minLength": 1, "writeOnly": True}
                if group == "required":
                    required.append(name)
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
