from datetime import timedelta

import pytest
from a13n_service.models.descriptions import describe_model
from a13n_service.models.discovery_paging import discovery_page
from a13n_service.models.domain import CreateModelProviderRequest
from a13n_service.models.providers import DiscoverModelsRequest, ModelDescriptionCollection, built_in_provider_registry
from a13n_service.models.service_common import ModelError

from .conftest import WORKSPACE_ID, actor


@pytest.mark.anyio
async def test_pages_are_deduplicated_and_bound_to_the_provider_revision(provider_service):
    provider = await provider_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelProviderRequest(type="openai", name="Pages", credential="secret"),
    )
    registry = built_in_provider_registry()
    catalog = ModelDescriptionCollection(
        items=tuple(describe_model(registry, "openai", name) for name in ["c", "a", "b", "a"])
    )
    first = discovery_page(catalog, DiscoverModelsRequest(limit=2), provider=provider, actor=actor())
    assert [item.upstream_model for item in first.items] == ["a", "b"]
    assert first.next_cursor
    second = discovery_page(catalog, DiscoverModelsRequest(cursor=first.next_cursor), provider=provider, actor=actor())
    assert [item.upstream_model for item in second.items] == ["c"]
    assert second.next_cursor is None
    with pytest.raises(ModelError, match="Restart discovery"):
        discovery_page(
            catalog,
            DiscoverModelsRequest(cursor=first.next_cursor),
            provider=provider.model_copy(update={"updated_at": provider.updated_at + timedelta(seconds=1)}),
            actor=actor(),
        )
