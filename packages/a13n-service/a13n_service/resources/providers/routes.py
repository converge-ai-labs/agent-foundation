"""One organization collection per provider kind, with an explicit `workspace_id`, and the registered types."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.providers.registry import ProviderKind
from a13n_service.resources.providers import service
from a13n_service.resources.providers.schemas import (
    Provider,
    ProviderCreate,
    ProviderPage,
    ProviderTest,
    ProviderTypePage,
    ProviderUpdate,
)
from a13n_service.resources.providers.tables import (
    ConnectorProviderRow,
    EnvironmentProviderRow,
    MemoryProviderRow,
    ModelProviderRow,
    ProviderRow,
    WebProviderRow,
)
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor

router = APIRouter(prefix="/api/v1", tags=["providers"])


def _add_routes(row_type: type[ProviderRow]) -> None:
    collection = f"/organizations/{{organization_id}}/{row_type.PROVIDER_KIND}-providers"
    item = collection + "/{provider_id}"

    @router.post(collection, response_model=Provider, status_code=201)
    async def create_provider(
        response: Response, organization_id: str, body: ProviderCreate, actor: Actor, runtime: CurrentRuntime
    ) -> Provider:
        result = await service.create_provider(
            runtime.storage, actor, row_type, organization_id, body, registry=runtime.registry, keys=runtime.keys
        )
        return tagged(response, result)

    @router.get(collection, response_model=ProviderPage)
    async def list_providers(
        organization_id: str,
        actor: Actor,
        runtime: CurrentRuntime,
        workspace_id: str | None = None,
        limit: PageLimit = 50,
        cursor: str | None = None,
    ) -> ProviderPage:
        return await service.list_providers(
            runtime.storage, actor, row_type, organization_id, workspace_id=workspace_id, limit=limit, cursor=cursor
        )

    @router.get(item, response_model=Provider)
    async def get_provider(
        response: Response, organization_id: str, provider_id: str, actor: Actor, runtime: CurrentRuntime
    ) -> Provider:
        result = await service.get_provider(runtime.storage, actor, row_type, organization_id, provider_id)
        return tagged(response, result)

    @router.patch(item, response_model=Provider)
    async def update_provider(
        response: Response,
        organization_id: str,
        provider_id: str,
        body: ProviderUpdate,
        actor: Actor,
        runtime: CurrentRuntime,
        if_match: IfMatch = None,
    ) -> Provider:
        """A `config` change must also replace or remove a stored credential: it never follows a new endpoint."""
        result = await service.update_provider(
            runtime.storage,
            actor,
            row_type,
            organization_id,
            provider_id,
            body,
            if_match=if_match,
            registry=runtime.registry,
            keys=runtime.keys,
        )
        return tagged(response, result)

    @router.post(item + "/test", response_model=ProviderTest)
    async def test_provider(
        organization_id: str, provider_id: str, actor: Actor, runtime: CurrentRuntime
    ) -> ProviderTest:
        return await service.test_provider(
            runtime.storage,
            actor,
            row_type,
            organization_id,
            provider_id,
            registry=runtime.registry,
            keys=runtime.keys,
            policy=runtime.endpoint_policy,
            settings=runtime.settings.providers,
        )


for _row_type in (ModelProviderRow, EnvironmentProviderRow, ConnectorProviderRow, WebProviderRow, MemoryProviderRow):
    _add_routes(_row_type)


@router.get("/provider-types/{kind}", response_model=ProviderTypePage)
async def list_provider_types(kind: ProviderKind, actor: Actor, runtime: CurrentRuntime) -> ProviderTypePage:
    return service.list_provider_types(runtime.registry, kind)
