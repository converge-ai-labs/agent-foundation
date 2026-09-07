"""OOMOL project-key managed accounts, paired with a read-only runtime catalog.

The project SDK publishes link, request polling, profile, and explicit-account
execution APIs. Personal aliases never select a managed account.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Literal
from urllib.parse import urlsplit

from anyio import to_thread
from jsonschema import Draft202012Validator, ValidationError
from pydantic import Field, JsonValue

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.management import canonical_json
from a13n_service.connectivity.tool_validation import validate_result

from ...contracts import (
    AdapterConnectionStatus,
    ConnectionBinding,
    ConnectionInspection,
    ConnectorProviderError,
    ConnectorTool,
    ConnectorToolOutcome,
    ConnectorToolPage,
    DiscoveredConnector,
    ProviderAccess,
    SetupContext,
    SetupStarted,
    StrictModel,
)
from ...http import ConnectorHttpClient
from ...validation import optional_string, path_segment, required_object, required_string
from ..configuration import ApiKeyCredentials, ConnectorKeys
from ..discovery import validate_discovered_setup
from .configuration import HOSTED_ENDPOINT, OpenConnectorConfiguration
from .runtime import OpenConnectorRuntime


class ProjectConfiguration(StrictModel):
    endpoint: Literal["https://connector.oomol.com"] = HOSTED_ENDPOINT
    enabled_services: ConnectorKeys


class ProjectCredentials(StrictModel):
    project_api_key: str = Field(
        min_length=1, max_length=4096, repr=False, json_schema_extra={"writeOnly": True, "format": "password"}
    )
    catalog_api_key: str = Field(
        min_length=1, max_length=4096, repr=False, json_schema_extra={"writeOnly": True, "format": "password"}
    )


class ProjectSetup(StrictModel):
    """OAuth configuration is selected by the service inside the OOMOL project."""


def validate_setup(configuration: JsonObject, connector_key: str, value: object) -> JsonObject:
    config = ProjectConfiguration.model_validate(configuration)
    if connector_key not in config.enabled_services:
        raise ValueError("connector_not_enabled")
    return ProjectSetup.model_validate(value).model_dump(mode="json")


class OpenConnectorProvider:
    compatibility_profile = "oomol_project_v1"
    # The published project API does not promise idempotent link creation.
    setup_replay_safe = False

    def __init__(
        self, http: ConnectorHttpClient, configuration: ProjectConfiguration, credentials: ProjectCredentials
    ) -> None:
        self._http = http
        self._configuration = configuration
        self._credentials = credentials
        self._catalog = OpenConnectorRuntime(
            http, OpenConnectorConfiguration(), ApiKeyCredentials(api_key=credentials.catalog_api_key)
        )

    async def aclose(self) -> None:
        pass

    async def request(self, method: Literal["GET", "POST"], path: str, *, body: JsonObject | None = None) -> JsonValue:
        raw = await self._http.request(
            method,
            endpoint=self._configuration.endpoint,
            path=f"/v1/saas/{path}",
            api_key=self._credentials.project_api_key,
            authentication="bearer",
            json_body=body,
            write=method == "POST",
        )
        envelope = required_object(raw)
        if envelope.get("success") is not True or "data" not in envelope:
            raise ConnectorProviderError("invalid_provider_response", outcome_unknown=method == "POST")
        return envelope["data"]

    def _require_service(self, service: str) -> None:
        if service not in self._configuration.enabled_services:
            raise ConnectorProviderError("connector_not_enabled")

    async def test(self) -> tuple[ProviderAccess, ...]:
        # A catalog read verifies its own credential. Project authority is verified by setup/profile,
        # since the published project API has no side-effect-free credential introspection route.
        await self.discover_connectors()
        return ("catalog_read",)

    async def discover_connectors(self) -> tuple[DiscoveredConnector, ...]:
        return tuple(
            DiscoveredConnector(
                key=p.service,
                name=p.name,
                setup_schema=ProjectSetup.model_json_schema()
                if "oauth2" in {m.lower() for m in p.authentication_methods}
                else {"not": {}},
                authentication_methods=("oauth2",) if "oauth2" in {m.lower() for m in p.authentication_methods} else (),
            )
            for p in await self._catalog.providers()
            if p.service in self._configuration.enabled_services
        )

    def tool_catalog(self, connector_key: str) -> ProjectToolCatalog:
        self._require_service(connector_key)
        return ProjectToolCatalog(self._catalog, connector_key)

    async def start_setup(
        self, *, setup: JsonObject, context: SetupContext, resume_ref: str | None = None
    ) -> SetupStarted:
        validate_setup(self._configuration.model_dump(mode="json"), context.connector_key, setup)
        await validate_discovered_setup(self.discover_connectors, context.connector_key, setup)
        body: JsonObject = {
            "userId": context.external_user_correlation,
            "service": context.connector_key,
            "alias": context.attempt_id,
        }
        try:
            value = required_object(
                await self.request("POST", "connected-accounts/link", body=body)
                if resume_ref is None
                else await self.request("GET", f"connection-requests/{path_segment(resume_ref)}")
            )
            if resume_ref is not None and value.get("id") != resume_ref:
                raise ConnectorProviderError("connection_substitution")
            self._verify_owner(value, context.connector_key, context.external_user_correlation)
            url = required_string(value, "authorizationUrl", max_length=4096)
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("invalid_authorization_url")
            return SetupStarted(
                setup_ref=required_string(value, "id"), redirect_url=url, supports_verified_callback=False
            )
        except (ValueError, ConnectorProviderError) as error:
            if isinstance(error, ConnectorProviderError):
                raise
            raise ConnectorProviderError("invalid_provider_response", outcome_unknown=True) from error

    async def inspect_setup(self, *, setup_ref: str, context: SetupContext) -> ConnectionInspection | None:
        value = required_object(await self.request("GET", f"connection-requests/{path_segment(setup_ref)}"))
        if required_string(value, "id") != setup_ref:
            raise ConnectorProviderError("connection_substitution")
        self._verify_owner(value, context.connector_key, context.external_user_correlation)
        status = required_string(value, "status")
        if status == "initiated":
            return None
        if status != "connected":
            raise ConnectorProviderError("setup_rejected")
        binding = ConnectionBinding(
            external_ref=required_string(value, "connectedAccountId"),
            connector_key=context.connector_key,
            external_user_correlation=context.external_user_correlation,
        )
        return await self.connect(binding).inspect()

    async def complete_setup(
        self, *, session_uri: str, context: SetupContext, expected_external_ref: str
    ) -> ConnectionInspection:
        raise ConnectorProviderError("callback_unsupported")

    def connect(self, binding: ConnectionBinding) -> ProjectConnection:
        self._require_service(binding.connector_key)
        return ProjectConnection(self, binding)

    @staticmethod
    def _verify_owner(value: JsonObject, service: str, correlation: str) -> None:
        if value.get("service") != service or value.get("externalUserId") != correlation:
            raise ConnectorProviderError("connection_substitution")


class ProjectToolCatalog:
    def __init__(self, catalog: OpenConnectorRuntime, service: str) -> None:
        self._catalog = catalog
        self._service = service

    async def discover_tools(self, *, cursor: str | None) -> ConnectorToolPage:
        if cursor is not None:
            raise ConnectorProviderError("invalid_cursor")
        actions = await self._catalog.actions(self._service)
        version = sha256(canonical_json({a.id: a.definition_digest for a in actions}).encode()).hexdigest()
        return ConnectorToolPage(
            items=tuple(
                ConnectorTool(
                    key=a.id,
                    provider_version=version,
                    description=a.description,
                    input_schema=a.input_schema,
                    output_schema=a.output_schema,
                )
                for a in actions
            ),
            provider_version=version,
        )


class ProjectConnection:
    def __init__(self, provider: OpenConnectorProvider, binding: ConnectionBinding) -> None:
        self._provider = provider
        self._binding = binding

    async def aclose(self) -> None:
        pass

    async def inspect(self) -> ConnectionInspection:
        b = self._binding
        value = required_object(
            await self._provider.request("GET", f"connected-accounts/{path_segment(b.external_ref)}/profile")
        )
        if value.get("connectedAccountId") != b.external_ref:
            raise ConnectorProviderError("connection_substitution")
        self._provider._verify_owner(value, b.connector_key, b.external_user_correlation)
        profile = required_object(value.get("profile"))
        # Only a successful live profile proves this exact account is usable; a lookup error fails closed.
        return ConnectionInspection(
            **b.model_dump(),
            status=AdapterConnectionStatus.ready,
            safe_metadata={"display_name": optional_string(profile.get("displayName"), max_length=128)},
            provider_version=self._provider.compatibility_profile,
        )

    async def discover_tools(self, *, cursor: str | None) -> ConnectorToolPage:
        await self.inspect()
        return await self._provider.tool_catalog(self._binding.connector_key).discover_tools(cursor=cursor)

    async def execute_tool(
        self, *, tool_key: str, provider_version: str, arguments: JsonObject, request_id: str
    ) -> ConnectorToolOutcome:
        page = await self.discover_tools(cursor=None)
        tool = next((tool for tool in page.items if tool.key == tool_key), None)
        if page.provider_version != provider_version or tool is None:
            raise ConnectorProviderError("incompatible_tool_version")
        await to_thread.run_sync(Draft202012Validator(tool.input_schema).validate, arguments)
        b = self._binding
        try:
            value = required_object(
                await self._provider.request(
                    "POST",
                    f"actions/{path_segment(tool_key)}",
                    body={
                        "userId": b.external_user_correlation,
                        "service": b.connector_key,
                        "connectedAccountId": b.external_ref,
                        "input": arguments,
                    },
                )
            )
            if value.get("actionId") != tool_key or "output" not in value:
                raise ValueError("invalid_action_result")
            result = value["output"]
            await to_thread.run_sync(validate_result, result)
            if tool.output_schema is not None:
                await to_thread.run_sync(Draft202012Validator(tool.output_schema).validate, result)
        except ConnectorProviderError as error:
            if not error.outcome_unknown:
                raise
            return ConnectorToolOutcome(kind="outcome_unknown", request_id=request_id)
        except (ValueError, ValidationError):
            return ConnectorToolOutcome(kind="outcome_unknown", request_id=request_id)
        return ConnectorToolOutcome(kind="succeeded", result=result, request_id=request_id)

    async def revoke(self, *, operation_id: str) -> None:
        # No project-key revoke endpoint is published. Local invalidation still succeeds.
        raise ConnectorProviderError("remote_revocation_unsupported")
