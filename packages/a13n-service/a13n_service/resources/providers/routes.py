"""One workspace collection per provider kind, and the registered types."""

from a13n_harness.providers.model.chatgpt import ChatGPTModel, discover_chatgpt_models
from fastapi import APIRouter, Response

from a13n_service.infra.db import short_session
from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.infra.outbound import open_http
from a13n_service.providers.registry import ProviderKind
from a13n_service.resources.providers import oauth, service
from a13n_service.resources.providers.oauth import (
    AuthorizationCallback,
    AuthorizationDisconnect,
    AuthorizationStart,
    AuthorizationStatus,
    ProviderAuthorizationRequest,
)
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


@router.get("/model-providers/{provider_id}/authorization", response_model=AuthorizationStatus)
async def model_authorization(
    workspace_id: WorkspaceId, provider_id: str, actor: Actor, runtime: CurrentRuntime
) -> AuthorizationStatus:
    return await oauth.status(runtime.storage, actor, workspace_id, provider_id)


@router.post("/model-providers/{provider_id}/authorize", response_model=AuthorizationStart)
async def authorize_model(
    workspace_id: WorkspaceId,
    provider_id: str,
    body: ProviderAuthorizationRequest,
    actor: Actor,
    runtime: CurrentRuntime,
) -> AuthorizationStart:
    return await oauth.authorize(runtime.storage, actor, workspace_id, provider_id, body, keys=runtime.keys)


@router.post("/model-providers/{provider_id}/authorization/callback", response_model=AuthorizationStatus)
async def complete_model_authorization(
    workspace_id: WorkspaceId, provider_id: str, body: AuthorizationCallback, actor: Actor, runtime: CurrentRuntime
) -> AuthorizationStatus:
    return await oauth.complete(
        runtime.storage,
        actor,
        workspace_id,
        provider_id,
        body,
        keys=runtime.keys,
        policy=runtime.endpoint_policy,
        settings=runtime.settings.providers,
    )


@router.delete("/model-providers/{provider_id}/authorization", response_model=AuthorizationDisconnect)
async def disconnect_model_authorization(
    workspace_id: WorkspaceId, provider_id: str, actor: Actor, runtime: CurrentRuntime
) -> AuthorizationDisconnect:
    return await oauth.disconnect(
        runtime.storage,
        actor,
        workspace_id,
        provider_id,
        keys=runtime.keys,
        policy=runtime.endpoint_policy,
        settings=runtime.settings.providers,
    )


@router.get("/model-providers/{provider_id}/models", response_model=list[ChatGPTModel])
async def discover_model_provider_models(
    workspace_id: WorkspaceId, provider_id: str, actor: Actor, runtime: CurrentRuntime
) -> tuple[ChatGPTModel, ...]:
    async with short_session(runtime.storage) as session:
        provider = await oauth.authorized_provider(session, actor, workspace_id, provider_id, "run")
        organization_id = provider.organization_id
    source = oauth.ChatGPTCredentialSource(runtime.storage, runtime.keys, provider_id, organization_id)
    async with open_http(
        runtime.endpoint_policy,
        timeout=runtime.settings.providers.model_timeout,
        max_bytes=runtime.settings.providers.response_bytes,
    ) as client:
        return await discover_chatgpt_models(credential_source=source, http_client=client)
