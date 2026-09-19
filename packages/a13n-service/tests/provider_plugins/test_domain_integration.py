from __future__ import annotations

from a13n_harness.providers.authentication import Authentication, CredentialMode
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.management import Environment
from a13n_harness.providers.environment.models import EnvironmentDescriptor, EnvironmentState
from a13n_harness.providers.model import ModelProviderDefinition, ProviderConfiguration
from a13n_service.process.components import Components
from a13n_service.process.environment import build_environment_catalog
from a13n_service.provider_plugins import ProviderCatalogs
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


def _describe(configuration: BaseModel) -> EnvironmentDescriptor:
    del configuration
    raise NotImplementedError


def _construct(
    *, configuration: BaseModel, environment_id: str, state: EnvironmentState | None, runtime: None
) -> Environment:
    del configuration, environment_id, state, runtime
    raise NotImplementedError


EXTERNAL_ENVIRONMENT = EnvironmentProviderDefinition(
    type="external_environment",
    display_name="External Environment",
    configuration_model=Configuration,
    credential_model=None,
    environment_models={"1": Configuration},
    construct=_construct,
    describe_environment=_describe,
    authentication=Authentication(mode=CredentialMode.forbidden),
    supports_managed=False,
)


def test_installed_environment_definition_enters_the_service_catalog() -> None:
    catalogs = ProviderCatalogs(
        environment=(EXTERNAL_ENVIRONMENT,),
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

    assert selected.require("external_environment") is EXTERNAL_ENVIRONMENT


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
