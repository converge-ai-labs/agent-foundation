"""OOMOL OpenConnector /v1 runtime, scoped to the supplied personal or runtime credential.

This surface does not attest Foundation owner correlations. It is deliberately separate
from ConnectorProviderRuntime and OOMOL's project-key SaaS account lifecycle.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Literal

from jsonschema import Draft202012Validator
from pydantic import Field, JsonValue

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.management import canonical_json
from a13n_service.connectivity.tool_validation import validate_result, validate_schema

from ...contracts import ConnectorProviderError, ConnectorToolOutcome, StrictModel
from ...http import ConnectorHttpClient
from ...validation import optional_string, path_segment, required_object, required_string
from ..configuration import ApiKeyCredentials
from ..discovery import DirectoryBudget
from .configuration import OpenConnectorConfiguration


class Provider(StrictModel):
    service: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    authentication_methods: tuple[str, ...] = Field(max_length=32)


class Action(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    service: str = Field(min_length=1, max_length=128)
    description: str = Field(max_length=16_384)
    input_schema: JsonObject
    output_schema: JsonObject | None
    definition_digest: str


class RuntimeConnection(StrictModel):
    id: str = Field(min_length=1, max_length=2048)
    service: str = Field(min_length=1, max_length=128)
    alias: str | None = Field(max_length=256)
    is_default: bool
    status: str = Field(min_length=1, max_length=64)


class OpenConnectorRuntime:
    def __init__(
        self, http: ConnectorHttpClient, configuration: OpenConnectorConfiguration, credentials: ApiKeyCredentials
    ) -> None:
        self._http = http
        self._configuration = configuration
        self._credentials = credentials

    async def _request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: JsonObject | None = None,
        headers: dict[str, str] | None = None,
    ) -> JsonValue:
        raw = await self._http.request(
            method,
            endpoint=self._configuration.endpoint,
            path=path,
            api_key=self._credentials.api_key,
            authentication="bearer",
            params=params,
            json_body=body,
            extra_headers=headers,
            write=method == "POST",
        )
        try:
            value = required_object(raw)
        except ValueError as error:
            raise ConnectorProviderError("invalid_provider_response", outcome_unknown=method == "POST") from error
        if value.get("success") is not True:
            raise ConnectorProviderError("invalid_provider_response", outcome_unknown=method == "POST")
        if "data" not in value:
            raise ConnectorProviderError("invalid_provider_response", outcome_unknown=method == "POST")
        return value["data"]

    async def _list(self, path: str, *, params: dict[str, str] | None = None) -> tuple[JsonObject, ...]:
        data = await self._request("GET", path, params=params)
        if not isinstance(data, list):
            raise ConnectorProviderError("invalid_provider_response")
        # /v1 returns a complete array, not a cursor page. Never silently truncate it.
        DirectoryBudget().record({"data": data}, len(data))
        return tuple(required_object(item) for item in data)

    async def providers(self) -> tuple[Provider, ...]:
        items = await self._list("/v1/providers")
        providers: list[Provider] = []
        seen: set[str] = set()
        for item in items:
            service = required_string(item, "service", max_length=128)
            if service in seen:
                raise ConnectorProviderError("invalid_provider_response")
            seen.add(service)
            methods = item.get("authTypes")
            if not isinstance(methods, list) or not all(isinstance(method, str) for method in methods):
                raise ConnectorProviderError("invalid_provider_response")
            providers.append(
                Provider(
                    service=service,
                    name=required_string(item, "displayName", max_length=128),
                    authentication_methods=tuple(method for method in methods if isinstance(method, str)),
                )
            )
        return tuple(providers)

    async def actions(self, service: str) -> tuple[Action, ...]:
        items = await self._list("/v1/actions", params={"service": service})
        actions = tuple(_action(item, service) for item in items)
        if len({action.id for action in actions}) != len(actions):
            raise ConnectorProviderError("invalid_provider_response")
        return actions

    async def action(self, service: str, action_id: str) -> Action:
        value = required_object(await self._request("GET", f"/v1/actions/{path_segment(action_id)}"))
        action = _action(value, service)
        if action.id != action_id:
            raise ConnectorProviderError("provider_mismatch")
        return action

    async def connections(self, service: str) -> tuple[RuntimeConnection, ...]:
        items = await self._list("/v1/apps")
        result: list[RuntimeConnection] = []
        seen: set[str] = set()
        for item in items:
            if required_string(item, "service", max_length=128) != service:
                continue
            identifier = required_string(item, "id")
            if identifier in seen or type(item.get("isDefault")) is not bool:
                raise ConnectorProviderError("invalid_provider_response")
            seen.add(identifier)
            result.append(
                RuntimeConnection(
                    id=identifier,
                    service=service,
                    alias=optional_string(item.get("alias"), max_length=256),
                    is_default=item["isDefault"] is True,
                    status=required_string(item, "status", max_length=64),
                )
            )
        return tuple(result)

    async def verify_connection(self, connection: RuntimeConnection) -> None:
        current = await self.connections(connection.service)
        exact = next((item for item in current if item.id == connection.id), None)
        if exact is None or exact != connection:
            raise ConnectorProviderError("connection_substitution")
        if exact.status != "active":
            raise ConnectorProviderError("connection_not_ready")
        # /v1 selects accounts by alias, never by an opaque connected-account ID.
        matches = (
            [item for item in current if item.alias == exact.alias]
            if exact.alias
            else [item for item in current if item.is_default]
        )
        if len(matches) != 1 or matches[0].id != exact.id:
            raise ConnectorProviderError("ambiguous_connection")

    async def execute_action(
        self, *, action: Action, connection: RuntimeConnection, arguments: JsonObject, request_id: str
    ) -> ConnectorToolOutcome:
        if connection.service != action.service:
            raise ConnectorProviderError("provider_mismatch")
        current = await self.action(action.service, action.id)
        if current != action:
            raise ConnectorProviderError("incompatible_tool_version")
        Draft202012Validator(action.input_schema).validate(arguments)
        await self.verify_connection(connection)
        headers = {"idempotency-key": request_id}
        if connection.alias:
            headers["x-oo-connector-alias"] = connection.alias
        try:
            result = await self._request(
                "POST", f"/v1/actions/{path_segment(action.id)}", body={"input": arguments}, headers=headers
            )
        except ConnectorProviderError as error:
            # A 409 can mean an idempotent request is still running or its outcome is unknown.
            # Without retaining raw provider errors, conservatively leave any write conflict unknown.
            if error.outcome_unknown or error.http_status == 409:
                return ConnectorToolOutcome(kind="outcome_unknown", request_id=request_id)
            raise
        try:
            validate_result(result)
        except ValueError:
            return ConnectorToolOutcome(kind="outcome_unknown", request_id=request_id)
        return ConnectorToolOutcome(kind="succeeded", result=result, request_id=request_id)


def _action(value: JsonObject, service: str) -> Action:
    identifier = required_string(value, "id", max_length=128)
    if required_string(value, "service", max_length=128) != service or not identifier.startswith(f"{service}."):
        raise ConnectorProviderError("provider_mismatch")
    input_schema = required_object(value.get("inputSchema"))
    output_schema = required_object(value["outputSchema"]) if value.get("outputSchema") is not None else None
    validate_schema(input_schema, require_object=False)
    if output_schema is not None:
        validate_schema(output_schema, require_object=False)
    return Action(
        id=identifier,
        service=service,
        description=optional_string(value.get("description"), max_length=16_384) or "",
        input_schema=input_schema,
        output_schema=output_schema,
        # OOMOL exposes current definitions, without immutable upstream action versions.
        definition_digest=sha256(canonical_json(value).encode()).hexdigest(),
    )
