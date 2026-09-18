from __future__ import annotations

from typing import cast

from a13n_environment import EnvironmentProvider
from a13n_harness.providers.model import ModelProviderDefinition, ProviderConfiguration
from a13n_service.connectivity.connectors.contracts import ConnectorProviderRuntime
from a13n_service.connectivity.connectors.http import ConnectorHttpClient
from a13n_service.process.components import Components
from a13n_service.process.environment import build_environment_catalog
from a13n_service.provider_plugins import (
    PROVIDER_EXTENSION_API_VERSION,
    ConnectorProviderRegistration,
    ProviderCatalogs,
    ProviderPluginRegistry,
)
from a13n_service.provider_plugins.connectors import build_connector_provider_registry
from a13n_service.settings import Settings
from pydantic import BaseModel, ConfigDict, Field


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str


class Credential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token: str


class AliasedConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    endpoint: str = Field(alias="baseUrl")
    tenant: str | None = "default-tenant"
    required_nullable: str | None


class AliasedModelConfiguration(ProviderConfiguration):
    deployment: str = Field(alias="deploymentName")
    tenant: str | None = "default-tenant"
    required_nullable: str | None


class ExternalEnvironmentProvider(EnvironmentProvider):
    @property
    def key(self) -> str:
        return "external.environment"

    @property
    def configuration_models(self) -> dict[str, type[BaseModel]]:
        return {"1": Configuration}

    def describe_configuration(self, configuration):
        raise NotImplementedError

    def create_environment(self, *, configuration: BaseModel, environment_id: str, state, runtime=None):
        del configuration, environment_id, state, runtime
        raise NotImplementedError


def test_external_environment_registration_enters_service_catalog() -> None:
    registry = ProviderPluginRegistry(api_version=PROVIDER_EXTENSION_API_VERSION)
    provider = ExternalEnvironmentProvider()
    registry.environment.register(provider)
    catalogs = ProviderCatalogs(
        environment=registry.environment.values(),
        model=(),
        connector=(),
        web=(),
        plugins=(),
    )

    selected = build_environment_catalog(
        Settings(environments={"provider_builtins": []}),
        Components(),
        catalogs,
    )

    assert selected.require("external.environment") is provider


def test_external_connector_registration_binds_role_transport_and_freezes() -> None:
    marker = cast(ConnectorProviderRuntime, object())
    transport = cast(ConnectorHttpClient, object())
    registration = ConnectorProviderRegistration(
        type="external_connector",
        display_name="External Connector",
        configuration_model=Configuration,
        credential_model=Credential,
        setup_validator=lambda configuration, connector_key, value: {
            "name": Configuration.model_validate(configuration).name,
            "connector_key": connector_key,
            "value": value,
        },
        factory=lambda http, configuration, credentials: (
            marker
            if http is transport and configuration == {"name": "account"} and credentials == {"token": "secret"}
            else cast(ConnectorProviderRuntime, object())
        ),
    )

    selected = build_connector_provider_registry((registration,), transport)
    implementation = selected.require("external_connector")

    assert implementation.configure({"name": "account"}, {"token": "secret"}) is marker
    assert implementation.validate_setup({"value": 1}, connector_key="github", configuration={"name": "account"}) == {
        "name": "account",
        "connector_key": "github",
        "value": {"value": 1},
    }
    try:
        selected.register(implementation)
    except RuntimeError as error:
        assert str(error) == "Connector Provider registry is frozen"
    else:
        raise AssertionError("startup registry remained mutable")


def test_external_connector_configuration_preserves_aliases_and_explicit_nulls() -> None:
    marker = cast(ConnectorProviderRuntime, object())
    captured = []

    def factory(_http, configuration, _credentials):
        captured.append(configuration)
        return marker

    registration = ConnectorProviderRegistration(
        type="aliased_connector",
        display_name="Aliased Connector",
        configuration_model=AliasedConfiguration,
        credential_model=Credential,
        setup_validator=lambda _configuration, _connector_key, _value: {},
        factory=factory,
    )
    selected = build_connector_provider_registry((registration,), cast(ConnectorHttpClient, object()))

    assert (
        selected.require("aliased_connector").configure(
            {"baseUrl": "https://connector.example", "tenant": None, "required_nullable": None},
            {"token": "secret"},
        )
        is marker
    )
    assert captured == [{"baseUrl": "https://connector.example", "tenant": None, "required_nullable": None}]


def test_external_model_configuration_preserves_aliases_and_explicit_nulls() -> None:
    def unused_builder(_provider, _http, _model_api):
        raise AssertionError("configuration validation must not construct a Provider")

    integration = ModelProviderDefinition(
        type="aliased_model",
        display_name="Aliased Model",
        configuration_model=AliasedModelConfiguration,
        credential_model=Credential,
        supported_model_apis=("openai.responses",),
        build_provider=unused_builder,
        endpoint="https://models.example.com/v1",
    )
    configuration = {
        "deploymentName": "production",
        "tenant": None,
        "required_nullable": None,
    }

    validated = integration.validate_configuration(configuration, credential_configured=True)

    assert validated.configuration == configuration
    assert (
        integration.validate_configuration(validated.configuration, credential_configured=True).configuration
        == configuration
    )
