"""Human configuration API; model tools never call these transport handlers."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from a13n_service.application_errors import ErrorCategory
from a13n_service.etags import resource_etag
from a13n_service.http_types import IdempotencyKey
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.iam.http.resource_dependencies import workspace_actor
from a13n_service.interactions.acceptance import RunAcceptanceReceipt
from a13n_service.request_runtime import get_control_runtime

from .application import ConfigurationApplicationCollection
from .conversations import (
    ConfigurationSessionCollection,
    ConfigurationSessionView,
    ConfigurationThreadCollection,
    ConfigurationThreadView,
)
from .domain import ConfigurationApplicationReceipt, ConfigurationDraft
from .inputs import ConfigurationInputRequest
from .persistence import failure
from .readiness import AssistantReadiness
from .requests import (
    ApplyDraftRequest,
    CreateConfigurationThreadRequest,
    CreateSessionRequest,
    DiscardDraftRequest,
    RebaseDraftRequest,
    UpdateConfigurationDraftRequest,
)
from .review import ConfigurationDraftReview
from .service import ConfigurationService

router = APIRouter(prefix="/api/v1", tags=["agent-configuration"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
WorkspaceActor = Annotated[AuthenticatedActor, Depends(workspace_actor)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=1, max_length=256)]
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=2048)]


def configuration(request: Request) -> ConfigurationService:
    control = get_control_runtime(request)
    if control is None or control.configuration is None:
        raise failure(
            "configuration_unavailable", "Configuration authoring is unavailable.", category=ErrorCategory.unavailable
        )
    return control.configuration


def draft_response(response: Response, draft: ConfigurationDraft) -> ConfigurationDraft:
    response.headers["ETag"] = resource_etag(draft.id, draft.updated_at)
    response.headers["Cache-Control"] = "no-store"
    return draft


@router.get("/workspaces/{workspace}/configuration-assistant/readiness")
async def readiness(request: Request, actor: WorkspaceActor, target_agent_id: str | None = None) -> AssistantReadiness:
    return await configuration(request).readiness.read(actor=actor, target_agent_id=target_agent_id)


@router.post("/workspaces/{workspace}/configuration-sessions", status_code=201)
async def create_session(
    request: Request, actor: WorkspaceActor, body: CreateSessionRequest, idempotency_key: IdempotencyKey
) -> ConfigurationSessionView:
    return await configuration(request).conversations.create_session(
        actor=actor, request=body, idempotency_key=idempotency_key
    )


@router.get("/workspaces/{workspace}/configuration-sessions")
async def list_sessions(
    request: Request, actor: WorkspaceActor, limit: Limit = 50, cursor: Cursor = None
) -> ConfigurationSessionCollection:
    return await configuration(request).conversations.list_sessions(actor=actor, limit=limit, cursor=cursor)


@router.get("/configuration-sessions/{session_id}")
async def get_session(request: Request, actor: Actor, session_id: str) -> ConfigurationSessionView:
    return await configuration(request).conversations.get_session(actor=actor, session_id=session_id)


@router.post("/configuration-sessions/{session_id}/threads", status_code=201)
async def create_thread(
    request: Request,
    actor: Actor,
    session_id: str,
    body: CreateConfigurationThreadRequest,
    idempotency_key: IdempotencyKey,
) -> ConfigurationThreadView:
    return await configuration(request).conversations.create_thread(
        actor=actor, session_id=session_id, request=body, idempotency_key=idempotency_key
    )


@router.get("/configuration-sessions/{session_id}/threads")
async def list_threads(
    request: Request, actor: Actor, session_id: str, limit: Limit = 50, cursor: Cursor = None
) -> ConfigurationThreadCollection:
    return await configuration(request).conversations.list_threads(
        actor=actor, session_id=session_id, limit=limit, cursor=cursor
    )


@router.get("/configuration-threads/{thread_id}")
async def get_thread(request: Request, actor: Actor, thread_id: str) -> ConfigurationThreadView:
    return await configuration(request).conversations.get_thread(actor=actor, thread_id=thread_id)


@router.post("/configuration-threads/{thread_id}/inputs", status_code=202)
async def submit_input(
    request: Request, actor: Actor, thread_id: str, body: ConfigurationInputRequest, idempotency_key: IdempotencyKey
) -> RunAcceptanceReceipt:
    return await configuration(request).inputs.submit(
        actor=actor, thread_id=thread_id, request=body, idempotency_key=idempotency_key
    )


@router.get("/configuration-drafts/{draft_id}")
async def get_draft(request: Request, response: Response, actor: Actor, draft_id: str) -> ConfigurationDraftReview:
    review = await configuration(request).reviews.get(actor=actor, draft_id=draft_id)
    draft_response(response, review)
    return review


@router.patch("/configuration-drafts/{draft_id}")
async def update_draft(
    request: Request,
    response: Response,
    actor: Actor,
    draft_id: str,
    body: UpdateConfigurationDraftRequest,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
) -> ConfigurationDraft:
    return draft_response(
        response,
        await configuration(request).drafts.update(
            actor=actor, draft_id=draft_id, request=body, idempotency_key=idempotency_key, if_match=if_match
        ),
    )


@router.post("/configuration-drafts/{draft_id}/rebase")
async def rebase_draft(
    request: Request,
    response: Response,
    actor: Actor,
    draft_id: str,
    body: RebaseDraftRequest,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
) -> ConfigurationDraft:
    return draft_response(
        response,
        await configuration(request).drafts.rebase(
            actor=actor, draft_id=draft_id, request=body, idempotency_key=idempotency_key, if_match=if_match
        ),
    )


@router.post("/configuration-drafts/{draft_id}/discard")
async def discard_draft(
    request: Request,
    response: Response,
    actor: Actor,
    draft_id: str,
    body: DiscardDraftRequest,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
) -> ConfigurationDraft:
    return draft_response(
        response,
        await configuration(request).drafts.discard(
            actor=actor, draft_id=draft_id, request=body, idempotency_key=idempotency_key, if_match=if_match
        ),
    )


@router.post("/configuration-drafts/{draft_id}/apply")
async def apply_draft(
    request: Request,
    actor: Actor,
    draft_id: str,
    body: ApplyDraftRequest,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
) -> ConfigurationApplicationReceipt:
    return await configuration(request).application.apply(
        actor=actor, draft_id=draft_id, request=body, idempotency_key=idempotency_key, if_match=if_match
    )


@router.get("/configuration-drafts/{draft_id}/applications")
async def list_applications(
    request: Request, actor: Actor, draft_id: str, limit: Limit = 50, cursor: Cursor = None
) -> ConfigurationApplicationCollection:
    return await configuration(request).application.list_applications(
        actor=actor, draft_id=draft_id, limit=limit, cursor=cursor
    )
