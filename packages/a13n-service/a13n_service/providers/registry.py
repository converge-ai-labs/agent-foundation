"""The provider definitions a deployment selected, keyed by (kind, type).

Definitions are the Harness's own, or the Service's qualification of one, so their schemas validate resource
`config` and `credential` exactly as execution will; the Service adds no second implementation catalogue.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal, overload

from a13n_environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.connector.definition import ConnectorProviderDefinition
from a13n_harness.providers.definition import ProviderDefinition
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.memory.definition import MemoryProviderDefinition
from a13n_harness.providers.model.definition import ModelProviderDefinition
from a13n_harness.providers.web.definition import WebProviderDefinition
from pydantic import JsonValue

from a13n_service.infra.errors import ServiceError
from a13n_service.providers.endpoints import check_endpoint, dialed_endpoint
from a13n_service.providers.model_settings import check_settings, settings_schema

type RegisteredProvider = ProviderDefinition | EnvironmentProviderDefinition

type ProviderKind = Literal["model", "environment", "connector", "web", "memory"]
type WebOperation = Literal["search", "scrape"]


def web_operations(definition: WebProviderDefinition) -> tuple[WebOperation, ...]:
    """The web tool operations a web provider type serves."""
    served: tuple[tuple[WebOperation, bool], ...] = (
        ("search", definition.supports_search),
        ("scrape", definition.supports_scrape),
    )
    return tuple(operation for operation, supported in served if supported)


@dataclass(frozen=True)
class Registry:
    models: ProviderCatalog[ModelProviderDefinition]
    environments: ProviderCatalog[EnvironmentProviderDefinition]
    connectors: ProviderCatalog[ConnectorProviderDefinition]
    web: ProviderCatalog[WebProviderDefinition]
    memory: ProviderCatalog[MemoryProviderDefinition]
    # The settings schema of each calling API a model type offers, derived once, at assembly.
    model_settings: Mapping[str, Mapping[str, JsonValue]]

    @classmethod
    def of(cls, definitions: Iterable[RegisteredProvider]) -> "Registry":
        """Group definitions by domain; a duplicate type within a domain or an unknown domain fails assembly.

        Deriving the settings schemas imports each calling API's SDK, here rather than inside a request.
        """
        models: list[ModelProviderDefinition] = []
        environments: list[EnvironmentProviderDefinition] = []
        connectors: list[ConnectorProviderDefinition] = []
        web: list[WebProviderDefinition] = []
        memory: list[MemoryProviderDefinition] = []
        for definition in definitions:
            match definition:
                case ModelProviderDefinition():
                    models.append(definition)
                case EnvironmentProviderDefinition():
                    environments.append(definition)
                case ConnectorProviderDefinition():
                    connectors.append(definition)
                case WebProviderDefinition():
                    web.append(definition)
                case MemoryProviderDefinition():
                    memory.append(definition)
                case _:
                    raise ValueError(f"Unsupported provider domain: {definition.DOMAIN}")
        apis = {api for definition in models for api in definition.supported_model_apis}
        return cls(
            ProviderCatalog(models),
            ProviderCatalog(environments),
            ProviderCatalog(connectors),
            ProviderCatalog(web),
            ProviderCatalog(memory),
            {api: settings_schema(api) for api in sorted(apis)},
        )

    def catalog(self, kind: ProviderKind) -> Mapping[str, RegisteredProvider]:
        match kind:
            case "model":
                return self.models
            case "environment":
                return self.environments
            case "connector":
                return self.connectors
            case "web":
                return self.web
            case "memory":
                return self.memory

    def types(self, kind: ProviderKind) -> list[RegisteredProvider]:
        return sorted(self.catalog(kind).values(), key=lambda definition: definition.type)

    @overload
    def get(self, kind: Literal["model"], type_: str) -> ModelProviderDefinition: ...
    @overload
    def get(self, kind: Literal["environment"], type_: str) -> EnvironmentProviderDefinition: ...
    @overload
    def get(self, kind: Literal["connector"], type_: str) -> ConnectorProviderDefinition: ...
    @overload
    def get(self, kind: Literal["web"], type_: str) -> WebProviderDefinition: ...
    @overload
    def get(self, kind: Literal["memory"], type_: str) -> MemoryProviderDefinition: ...
    @overload
    def get(self, kind: ProviderKind, type_: str) -> RegisteredProvider: ...
    def get(self, kind: ProviderKind, type_: str) -> RegisteredProvider:
        """The definition a resource's `type` selects; an unregistered type is an unavailable dependency."""
        definition = self.catalog(kind).get(type_)
        if definition is None:
            raise ServiceError(
                "unavailable", f"Provider type {kind}:{type_} is not available", {"dependency": f"{kind}:{type_}"}
            )
        return definition

    def check_model_settings(
        self, model_api: str, settings: Mapping[str, JsonValue], *, field: str
    ) -> dict[str, JsonValue]:
        """Refuse settings the calling API does not accept, naming the most relevant failing path under `field`; an
        API no model type offers any more is an unavailable dependency, as an unregistered type is."""
        schema = self.model_settings.get(model_api)
        if schema is None:
            raise ServiceError(
                "unavailable", f"Model API {model_api} is not available", {"dependency": f"model_api:{model_api}"}
            )
        return check_settings(schema, settings, field=field)

    def environment_endpoint(self, type_: str, config: Mapping[str, JsonValue]) -> str | None:
        """The URL of the endpoint an environment account names that Service processes dial, a remote Docker
        engine; None when the account reaches only a backend the operator chose."""
        definition = self.get("environment", type_)
        return dialed_endpoint(definition.configuration_model.model_validate(config))

    async def check_environment_endpoint(
        self, type_: str, config: Mapping[str, JsonValue], policy: EndpointPolicy
    ) -> None:
        """Refuse, before any dial, an environment account whose endpoint the operator's policy denies."""
        endpoint = self.environment_endpoint(type_, config)
        if endpoint is not None:
            await check_endpoint(type_, endpoint, policy)
