"""One workspace collection per provider kind, and the registered types."""

from urllib.parse import parse_qs, urlsplit

from a13n_harness.providers.model.chatgpt import ChatGPTModel, discover_chatgpt_models
from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse

from a13n_service.infra.db import short_session
from a13n_service.infra.errors import invalid
from a13n_service.infra.http import IfMatch, PageLimit, answer_headers, tagged
from a13n_service.infra.outbound import open_http
from a13n_service.providers.registry import ProviderKind
from a13n_service.resources.providers import callback, oauth, service
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
from a13n_service.tenancy.requests import Actor, WorkspaceId, limit_guessing

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
    response: Response,
    workspace_id: WorkspaceId,
    provider_id: str,
    body: ProviderAuthorizationRequest,
    actor: Actor,
    runtime: CurrentRuntime,
) -> AuthorizationStart:
    result = await oauth.authorize(
        runtime.storage,
        actor,
        workspace_id,
        provider_id,
        body,
        keys=runtime.keys,
        settings=runtime.settings.providers,
        public_origin=runtime.settings.server.public_origin,
    )
    if result.method == "browser_callback":
        name, value = callback.browser_cookie(result, actor, workspace_id, provider_id, keys=runtime.keys)
        response.set_cookie(
            name,
            value,
            max_age=600,
            path=callback.cookie_path(parse_qs(urlsplit(result.authorization_url).query)["redirect_uri"][0]),
            secure=True,
            httponly=True,
            samesite="lax",
        )
    return result


@router.get(callback.CALLBACK_PATH.removeprefix("/api/v1"), include_in_schema=False)
async def complete_browser_model_authorization(request: Request, runtime: CurrentRuntime) -> Response:
    # Flow-cookie authentication replaces the Strict login cookie on this cross-site GET.
    # Errors also cannot leak the callback query.
    answer = answer_headers(request)
    answer.headers["Cache-Control"] = "no-store"
    answer.headers["Referrer-Policy"] = "no-referrer"
    await limit_guessing(request, "model_provider_callback")
    try:
        query = request.scope["query_string"].decode("ascii")
    except UnicodeDecodeError:
        raise invalid("callback", "invalid authorization response") from None
    name = callback.cookie_name(request.query_params.get("state", ""))
    _, redirect_uri = await callback.complete(
        runtime.storage,
        runtime.access,
        query=query,
        cookie=request.cookies.get(name),
        keys=runtime.keys,
        settings=runtime.settings.providers,
        policy=runtime.endpoint_policy,
        public_origin=runtime.settings.server.public_origin,
    )
    answer.delete_cookie(name, path=callback.cookie_path(redirect_uri), secure=True, httponly=True, samesite="lax")
    return HTMLResponse(
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>ChatGPT sign-in complete</title>'
        "<h1>ChatGPT sign-in complete</h1><p>You can close this tab and return to Agent Foundation.</p></html>"
    )


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
