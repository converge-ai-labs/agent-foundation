"""Every Provider type endpoint projects the shared definition core the same way."""

import pytest
from a13n_harness.providers.connector.builtins import BUILT_IN_CONNECTOR_PROVIDERS
from a13n_harness.providers.environment.builtins import BUILT_IN_ENVIRONMENT_PROVIDERS
from a13n_harness.providers.memory.builtins import BUILT_IN_MEMORY_PROVIDERS
from a13n_harness.providers.model.builtins import BUILT_IN_MODEL_PROVIDERS
from a13n_harness.providers.web.builtins import built_in_web_providers
from a13n_service.connectivity.connectors.domain import ConnectorProviderMetadata
from a13n_service.connectivity.connectors.router import router as connector_router
from a13n_service.environments.domain import EnvironmentProviderMetadata
from a13n_service.environments.router import router as environment_router
from a13n_service.memory.domain import MemoryProviderMetadata
from a13n_service.memory.provider_router import router as memory_router
from a13n_service.models.providers import ModelProviderMetadata
from a13n_service.models.router import router as model_router
from a13n_service.provider_metadata import ProviderMetadata, ProviderMetadataCollection
from a13n_service.web.domain import WebProviderMetadata
from a13n_service.web.router import router as web_router
from fastapi.routing import APIRoute

DOMAINS = [
    pytest.param("web", built_in_web_providers(), WebProviderMetadata.describe, web_router, id="web"),
    pytest.param("model", BUILT_IN_MODEL_PROVIDERS, ModelProviderMetadata.describe, model_router, id="model"),
    pytest.param("memory", BUILT_IN_MEMORY_PROVIDERS, MemoryProviderMetadata.describe, memory_router, id="memory"),
    pytest.param(
        "connector",
        BUILT_IN_CONNECTOR_PROVIDERS,
        ConnectorProviderMetadata.describe,
        connector_router,
        id="connector",
    ),
    pytest.param(
        "environment",
        BUILT_IN_ENVIRONMENT_PROVIDERS,
        lambda definition: EnvironmentProviderMetadata.describe(definition, deployment_managed=False),
        environment_router,
        id="environment",
    ),
]


@pytest.mark.parametrize(("domain", "definitions", "describe", "router"), DOMAINS)
def test_every_domain_projects_the_same_core(domain, definitions, describe, router):
    for definition in definitions:
        metadata = describe(definition)
        assert isinstance(metadata, ProviderMetadata)
        assert metadata.type == definition.type
        assert metadata.display_name == definition.display_name
        assert metadata.authentication == definition.authentication
        assert metadata.setup_url == definition.setup_url
        assert metadata.setup_label == definition.setup_label
        assert metadata.configuration_schema == definition.configuration_model.model_json_schema()


@pytest.mark.parametrize(("domain", "definitions", "describe", "router"), DOMAINS)
def test_credential_schemas_are_write_only_and_absent_without_a_credential(domain, definitions, describe, router):
    for definition in definitions:
        credential_schema = describe(definition).credential_schema
        if definition.credential_model is None:
            assert credential_schema is None
        else:
            assert credential_schema == {**definition.credential_model.model_json_schema(), "writeOnly": True}


@pytest.mark.parametrize(("domain", "definitions", "describe", "router"), DOMAINS)
def test_type_routes_share_one_collection_envelope_and_one_single_type_shape(domain, definitions, describe, router):
    metadata_model = type(describe(definitions[0]))
    routes = {
        route.path: route.response_model
        for route in router.routes
        if isinstance(route, APIRoute) and route.methods == {"GET"}
    }
    assert routes[f"/api/v1/{domain}-provider-types"] == ProviderMetadataCollection[metadata_model]
    assert routes[f"/api/v1/{domain}-provider-types/{{provider_type}}"] is metadata_model
