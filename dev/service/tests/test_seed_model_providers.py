from unittest.mock import AsyncMock

import pytest
from a13n_service.ids import new_object_id
from a13n_service.models.domain import CreateModelRequest
from a13n_service.models.providers import built_in_model_provider_catalog

from dev.service.seed_model_providers import MODEL_EXAMPLES, seed_model_providers, seed_provider_models


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_every_builtin_provider_has_a_valid_fictional_seed_connection():
    registry = built_in_model_provider_catalog()
    created = []

    async def create(method, path, *, expected, json):
        assert method == "POST" and expected == 201
        integration = registry.require(json["type"])
        integration.validate_configuration(json["configuration"], credential_configured=json["credential"] is not None)
        integration.bind(json["configuration"], json["credential"])
        item = {**json, "id": f"mp_{json['type']}"}
        created.append(item)
        return item

    client = AsyncMock()
    client.collection.return_value = []
    client.request.side_effect = create
    result = await seed_model_providers(client, "/api/v1/workspaces/ws_test")
    assert set(result) == {definition.type for definition in registry.values()}
    assert "xai" not in result
    client.collection.return_value = created
    client.request.reset_mock()
    assert await seed_model_providers(client, "/api/v1/workspaces/ws_test") == result
    client.request.assert_not_called()


@pytest.mark.anyio
async def test_every_builtin_provider_has_a_disabled_example_model():
    registry = built_in_model_provider_catalog()
    providers = {definition.type: new_object_id("mp") for definition in registry.values()}
    assert set(MODEL_EXAMPLES) == set(providers)
    created = []
    pricing = {"tiers": [{"above": None, "rates": {"input": "5", "output": "30"}}]}

    async def request(method, path, *, expected=200, json=None):
        if method == "GET":
            assert path.endswith("/model-catalog")
            return {
                "items": [
                    {
                        "ref": {"provider": "openai", "model": "gpt-5.6"},
                        "declarations": {"pricing": pricing},
                    }
                ]
            }
        assert method == "POST" and path.endswith("/models") and expected == 201
        model = CreateModelRequest.model_validate(json)
        definition = registry.require(
            next(kind for kind, provider_id in providers.items() if provider_id == model.provider_id)
        )
        assert model.model_api == definition.supported_model_apis[0]
        assert not model.enabled
        item = {**json, "id": f"mdl_{json['key']}"}
        created.append(item)
        return item

    client = AsyncMock()
    client.collection.return_value = []
    client.request.side_effect = request
    result = await seed_provider_models(client, "/api/v1/workspaces/ws_test", providers)
    assert set(result) == set(providers)
    assert len(created) == len(providers)
    assert next(item for item in created if item["key"] == "demo-openai")["declarations"] == {"pricing": pricing}
    assert next(item for item in created if item["key"] == "demo-ollama")["catalog_ref"] is None
    client.collection.return_value = created
    client.request.reset_mock()
    assert await seed_provider_models(client, "/api/v1/workspaces/ws_test", providers) == result
    client.request.assert_not_called()
