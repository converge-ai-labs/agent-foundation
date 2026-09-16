"""Bot installation checks and provider conversation discovery."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.accounts.domain import Account
from a13n_service.connectivity.errors import NativeError
from a13n_service.connectivity.inspection import ConversationPage
from a13n_service.http_types import IdempotencyKey
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.iam.http.resource_dependencies import WorkspaceId
from a13n_service.request_runtime import get_process_runtime

from .collection import BotCollection, BotPlatform, BotSetupCondition, BotSummary
from .domain import ActivateBotRequest, BotCheck, BotCheckHistory, BotCheckRequest, BotSetup
from .history import BotThreadCollection
from .reply_queries import BotReplyCollection
from .service import BotService
from .setup_tests import BotTest, BotTestHistory, CreateBotTest

router = APIRouter(prefix="/api/v1/application-accounts/{account_id}/bot", tags=["bots"])
collection_router = APIRouter(prefix="/api/v1", tags=["bots"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]


def _service(request: Request) -> BotService:
    runtime = get_process_runtime(request)
    if runtime is None or runtime.bots is None:
        raise NativeError("bot_checks_unavailable", "Bot checks are unavailable.", category=ErrorCategory.unavailable)
    return runtime.bots


@router.post("/activate", response_model=Account)
async def activate_bot(request: Request, actor: Actor, account_id: str, body: ActivateBotRequest) -> Account:
    return await _service(request).activate(actor=actor, account_id=account_id, request=body)


@router.get("/setup", response_model=BotSetup)
async def bot_setup(request: Request, actor: Actor, account_id: str) -> BotSetup:
    return await _service(request).setup(actor=actor, account_id=account_id)


@router.post("/checks", response_model=BotCheck)
async def check_bot(request: Request, actor: Actor, account_id: str, body: BotCheckRequest) -> BotCheck:
    return await _service(request).check(actor=actor, account_id=account_id, request=body)


@router.get("/checks/latest", response_model=BotCheckHistory)
async def latest_bot_check(
    request: Request,
    actor: Actor,
    account_id: str,
    conversation_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> BotCheckHistory:
    return await _service(request).latest(actor=actor, account_id=account_id, conversation_id=conversation_id)


@router.get("/conversations", response_model=ConversationPage)
async def discover_bot_conversations(
    request: Request,
    actor: Actor,
    account_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    cursor: Annotated[str | None, Query(min_length=1, max_length=2048)] = None,
) -> ConversationPage:
    return await _service(request).conversations(actor=actor, account_id=account_id, limit=limit, cursor=cursor)


@router.get("/threads", response_model=BotThreadCollection)
async def list_bot_conversation_threads(
    request: Request,
    actor: Actor,
    account_id: str,
    target_id: Annotated[str | None, Query(min_length=1, max_length=72)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(min_length=1, max_length=4096)] = None,
) -> BotThreadCollection:
    return await _service(request).threads(
        actor=actor, account_id=account_id, target_id=target_id, limit=limit, cursor=cursor
    )


@router.get("/replies", response_model=BotReplyCollection)
async def list_bot_reply_observations(
    request: Request,
    actor: Actor,
    account_id: str,
    run_id: Annotated[str, Query(min_length=1, max_length=72)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(min_length=1, max_length=4096)] = None,
) -> BotReplyCollection:
    return await _service(request).replies(
        actor=actor, account_id=account_id, run_id=run_id, limit=limit, cursor=cursor
    )


@router.post("/tests", response_model=BotTest)
async def create_bot_setup_test(
    request: Request,
    actor: Actor,
    account_id: str,
    body: CreateBotTest,
    idempotency_key: IdempotencyKey,
) -> BotTest:
    return await _service(request).create_test(
        actor=actor, account_id=account_id, request=body, idempotency_key=idempotency_key
    )


@router.get("/tests/latest", response_model=BotTestHistory)
async def latest_bot_setup_test(request: Request, actor: Actor, account_id: str) -> BotTestHistory:
    return await _service(request).test(actor=actor, account_id=account_id, test_id=None)


@router.get("/tests/{test_id}", response_model=BotTestHistory)
async def get_bot_setup_test(request: Request, actor: Actor, account_id: str, test_id: str) -> BotTestHistory:
    return await _service(request).test(actor=actor, account_id=account_id, test_id=test_id)


@collection_router.get("/workspaces/{workspace}/bots", response_model=BotCollection)
async def bot_collection(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(min_length=1, max_length=4096)] = None,
    platform: BotPlatform | None = None,
    condition: BotSetupCondition | None = None,
    search: Annotated[str | None, Query(max_length=256)] = None,
) -> BotCollection:
    return await _service(request).collection(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
        platform=platform,
        condition=condition,
        search=search,
    )


@router.get("/summary", response_model=BotSummary)
async def bot_summary(request: Request, actor: Actor, account_id: str) -> BotSummary:
    return await _service(request).summary(actor=actor, account_id=account_id)
