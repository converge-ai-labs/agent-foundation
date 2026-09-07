"""OOMOL Provider and Action catalogs used by managed connections."""

from __future__ import annotations

from hashlib import sha256

from pydantic import Field

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.management import canonical_json
from a13n_service.connectivity.tool_validation import validate_schema

from ...contracts import ConnectorProviderError, StrictModel
from ...http import ConnectorHttpClient
from ...validation import optional_string, provider_response_errors, required_object, required_string
from ..discovery import DirectoryBudget


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


HOSTED_ENDPOINT = "https://connector.oomol.com"


class OpenConnectorCatalog:
    """Read-only directory access for the managed Connector Provider."""

    def __init__(self, http: ConnectorHttpClient, api_key: str) -> None:
        self._http = http
        self._api_key = api_key

    async def _list(self, path: str, *, params: dict[str, str] | None = None) -> tuple[JsonObject, ...]:
        with provider_response_errors():
            raw = await self._http.request(
                "GET",
                endpoint=HOSTED_ENDPOINT,
                path=path,
                api_key=self._api_key,
                authentication="bearer",
                params=params,
            )
            envelope = required_object(raw)
            if envelope.get("success") is not True or "data" not in envelope:
                raise ConnectorProviderError("invalid_provider_response")
            data = envelope["data"]
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
