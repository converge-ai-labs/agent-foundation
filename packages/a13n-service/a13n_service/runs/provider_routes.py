"""Provider deletion routes spanning configuration and retained execution references."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import IfMatch
from a13n_service.resources.providers.tables import (
    BrowserProviderRow,
    ConnectorProviderRow,
    MemoryProviderRow,
    ModelProviderRow,
    ProviderRow,
    WebProviderRow,
)
from a13n_service.runs.provider_deletion import delete_provider
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1", tags=["providers"])


def _add_route(row_type: type[ProviderRow]) -> None:
    kind = row_type.PROVIDER_KIND

    @router.delete(
        f"/{kind}-providers/{{provider_id}}",
        status_code=204,
        name=f"delete_{kind}_provider",
        openapi_extra={"x-a13n-mcp": True},
    )
    async def remove(
        workspace_id: WorkspaceId,
        provider_id: str,
        actor: Actor,
        runtime: CurrentRuntime,
        if_match: IfMatch = None,
    ) -> Response:
        await delete_provider(runtime.storage, actor, row_type, workspace_id, provider_id, if_match=if_match)
        return Response(status_code=204)


for _row_type in (ModelProviderRow, ConnectorProviderRow, WebProviderRow, MemoryProviderRow, BrowserProviderRow):
    _add_route(_row_type)
