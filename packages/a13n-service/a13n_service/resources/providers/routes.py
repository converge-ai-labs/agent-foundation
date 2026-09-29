"""One workspace collection per provider kind, and the registered types."""

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
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1", tags=["providers"])


def _add_routes(row_type: type[ProviderRow]) -> None:
    kind = row_type.PROVIDER_KIND
    collection = f"/{kind}-providers"
    item = collection + "/{provider_id}"

    # Every kind shares these handlers; name the kind so each operation stays distinguishable.
    @router.post(collection, response_model=Provider, status_code=201, summary=f"Create {kind} provider")
    async def create_provider(
        response: Response, workspace_id: WorkspaceId, body: ProviderCreate, actor: Actor, runtime: CurrentRuntime
    ) -> Provider:
        result = await service.create_provider(
            runtime.storage, actor, row_type, workspace_id, body, registry=runtime.registry, keys=runtime.keys
        )
        return tagged(response, result)

    @router.get(collection, response_model=ProviderPage, summary=f"List {kind} providers")
    async def list_providers(
        workspace_id: WorkspaceId,
        actor: Actor,
        runtime: CurrentRuntime,
        limit: PageLimit = 50,
        cursor: str | None = None,
    ) -> ProviderPage:
        return await service.list_providers(runtime.storage, actor, row_type, workspace_id, limit=limit, cursor=cursor)

    @router.get(item, response_model=Provider, summary=f"Get {kind} provider")
    async def get_provider(
        response: Response, workspace_id: WorkspaceId, provider_id: str, actor: Actor, runtime: CurrentRuntime
    ) -> Provider:
        result = await service.get_provider(runtime.storage, actor, row_type, workspace_id, provider_id)
        return tagged(response, result)

    @router.patch(item, response_model=Provider, summary=f"Update {kind} provider")
    async def update_provider(
        response: Response,
        workspace_id: WorkspaceId,
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
            workspace_id,
            provider_id,
            body,
            if_match=if_match,
            registry=runtime.registry,
            keys=runtime.keys,
        )
        return tagged(response, result)

    @router.post(item + "/test", response_model=ProviderTest, summary=f"Test {kind} provider")
    async def test_provider(
        workspace_id: WorkspaceId, provider_id: str, actor: Actor, runtime: CurrentRuntime
    ) -> ProviderTest:
        return await service.test_provider(
            runtime.storage,
            actor,
            row_type,
            workspace_id,
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
