"""Composio app metadata and hosted authentication configuration selection."""

from __future__ import annotations

import re
from dataclasses import dataclass

from anyio import Semaphore, create_task_group
from jsonschema import Draft202012Validator
from pydantic import JsonValue

from a13n_harness.providers.connector.contracts import JsonObject

from ..contracts import ConnectorProviderError, ConnectorTool, ConnectorToolPage, DiscoveredConnector
from ..directory import DirectoryBudget, directory_items, is_credential_field
from ..http import ConnectorHttpClient
from ..validation import optional_string, path_segment, required_object, required_string
from .configuration import COMPOSIO_ENDPOINT, ComposioSetup
from .output_schema import corrected_output_schema

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

    async def prepare_setup(self, key: str, setup: JsonObject) -> AuthConfiguration:
        item = await self._toolkit(key)
        configurations = await self.configurations()
        configured = await self._validate_setup(key, setup, item, configurations)
        return await self.resolve_auth_config(key, configured.auth_config_id, configurations=configurations)

    async def _validate_setup(
        self, key: str, setup: JsonObject, item: JsonObject, configurations: tuple[AuthConfiguration, ...]
    ) -> ComposioSetup:
        configured = ComposioSetup.model_validate(setup)
        connector = connector_metadata(item, configurations)
        if connector.unavailable_reason:
            raise ConnectorProviderError("connector_setup_unavailable")
        version = required_object(required_object(connector.setup_schema["properties"])["toolkit_version"])
        if version["const"] != configured.toolkit_version:
            # Current metadata may advance; prove the saved version still has matching definitions.
            page = await ComposioToolCatalog(
                self._http, self._api_key, key, provider_version=configured.toolkit_version
            ).discover_tools(cursor=None)
            if not page.items:
                raise ConnectorProviderError("incompatible_toolkit_version")
            version["const"] = configured.toolkit_version
        if not Draft202012Validator(connector.setup_schema).is_valid(setup):
            raise ConnectorProviderError("invalid_setup_options")
        return configured

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
        self, key: str, selection: str, *, configurations: tuple[AuthConfiguration, ...]
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


class ComposioToolCatalog:
    """Read a toolkit's definitions without an external account binding."""

    def __init__(
        self,
        http: ConnectorHttpClient,
        api_key: str,
        connector_key: str,
        *,
        provider_version: str | None = None,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._connector_key = connector_key
        if provider_version is not None and TOOLKIT_VERSION.fullmatch(provider_version) is None:
            raise ConnectorProviderError("incompatible_toolkit_version")
        self._catalog_version = provider_version

    async def discover_tools(self, *, cursor: str | None) -> ConnectorToolPage:
        if self._catalog_version is None:
            toolkit = required_object(
                await self._http.request(
                    "GET",
                    endpoint=COMPOSIO_ENDPOINT,
                    path=f"/api/v3.1/toolkits/{path_segment(self._connector_key)}",
                    api_key=self._api_key,
                )
            )
            if required_string(toolkit, "slug", max_length=128) != self._connector_key:
                raise ConnectorProviderError("provider_mismatch")
            version = required_string(required_object(toolkit.get("meta")), "version", max_length=128)
            if TOOLKIT_VERSION.fullmatch(version) is None:
                raise ConnectorProviderError("incompatible_toolkit_version")
            self._catalog_version = version
        version = self._catalog_version
        params = {
            "toolkit_slug": self._connector_key,
            f"toolkit_versions[{self._connector_key}]": version,
            "limit": "100",
        }
        if cursor is not None:
            params["cursor"] = cursor
        value = required_object(
            await self._http.request(
                "GET",
                endpoint=COMPOSIO_ENDPOINT,
                path="/api/v3.1/tools",
                api_key=self._api_key,
                params=params,
            )
        )
        items = value.get("items")
        if not isinstance(items, list) or len(items) > 100:
            raise ConnectorProviderError("invalid_provider_response")
        entries = [required_object(item) for item in items]
        keys = [required_string(item, "slug", max_length=128) for item in entries]
        if len(keys) != len(set(keys)):
            raise ConnectorProviderError("invalid_provider_response")
        tools: dict[str, ConnectorTool] = {}
        limit = Semaphore(32)

        async def load(key: str, entry: JsonObject) -> None:
            # Current directory pages already carry complete, versioned schemas.
            # Sparse directory entries still need the individual detail endpoint.
            if {"toolkit", "version", "input_parameters", "output_parameters"} <= entry.keys():
                tools[key] = parse_detail(key, entry)
            else:
                async with limit:
                    tools[key] = await detail_for(key)

        async def detail_for(key: str) -> ConnectorTool:
            detail = required_object(
                await self._http.request(
                    "GET",
                    endpoint=COMPOSIO_ENDPOINT,
                    path=f"/api/v3.1/tools/{path_segment(key)}",
                    api_key=self._api_key,
                    params={"version": version},
                )
            )
            return parse_detail(key, detail)

        def parse_detail(key: str, detail: JsonObject) -> ConnectorTool:
            if (
                required_string(detail, "slug", max_length=128) != key
                or required_string(required_object(detail.get("toolkit")), "slug", max_length=128)
                != self._connector_key
                or required_string(detail, "version", max_length=128) != version
            ):
                raise ConnectorProviderError("incompatible_tool_version")
            return _tool(detail)

        try:
            async with create_task_group() as group:
                for key, entry in zip(keys, entries, strict=True):
                    group.start_soon(load, key, entry)
        except* (ConnectorProviderError, ValueError) as failures:
            raise failures.exceptions[0] from None
        return ConnectorToolPage(
            items=tuple(tools[key] for key in keys),
            next_cursor=optional_string(value.get("next_cursor")),
            provider_version=version,
        )


def _tool(value: JsonObject) -> ConnectorTool:
    key = required_string(value, "slug", max_length=128)
    version = required_string(value, "version", max_length=128)
    return ConnectorTool(
        provider_version=version,
        key=key,
        description=optional_string(value.get("description"), max_length=16_384) or "",
        input_schema=required_object(value.get("input_parameters")),
        output_schema=(
            corrected_output_schema(required_object(value["output_parameters"]), tool_key=key, version=version)
            if value.get("output_parameters") is not None
            else None
        ),
        annotations={},
    )
