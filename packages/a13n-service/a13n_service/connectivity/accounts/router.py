"""Application Account management routes under ``/api/v1``."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.errors import NativeError
from a13n_service.etags import resource_etag
from a13n_service.http_types import IdempotencyKey
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.iam.http.resource_dependencies import WorkspaceId
from a13n_service.request_runtime import get_connectivity_control_runtime

from .domain import (
    Account,
    AccountCollection,
    AccountCommandRequest,
    AccountProviderDefinitionCollection,
    AccountStatus,
    CreateAccountRequest,
    EventConnectionStatus,
    ReplaceAccountCredentialsRequest,
    UpdateAccountRequest,
)
from .service import AccountService

router = APIRouter(prefix="/api/v1", tags=["connectivity-management"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]


def _service(request: Request) -> AccountService:
    runtime = get_connectivity_control_runtime(request)
    if runtime is None:
        raise NativeError(
            "account_management_unavailable", "Account Management is unavailable.", category=ErrorCategory.unavailable
        )
    return runtime.accounts


def _set_etag(response: Response, resource: Account) -> None:
    response.headers["ETag"] = resource_etag(resource.id, resource.updated_at)


@router.get(
    "/workspaces/{workspace}/application-account-provider-types",
    response_model=AccountProviderDefinitionCollection,
)
async def account_provider_types(
    request: Request, actor: Actor, workspace_id: WorkspaceId
) -> AccountProviderDefinitionCollection:
    return await _service(request).provider_types(actor=actor, workspace_id=workspace_id)


@router.post(
    "/workspaces/{workspace}/application-accounts",
    response_model=Account,
    status_code=status.HTTP_201_CREATED,
)
async def create_account(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: WorkspaceId,
    body: CreateAccountRequest,
    idempotency_key: IdempotencyKey,
) -> Account:
    resource = await _service(request).create_account(
        actor=actor,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    _set_etag(response, resource)
    return resource


@router.get("/workspaces/{workspace}/application-accounts", response_model=AccountCollection)
async def list_accounts(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> AccountCollection:
    return await _service(request).list_accounts(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/application-accounts/{account_id}", response_model=Account)
async def get_account(request: Request, response: Response, actor: Actor, account_id: str) -> Account:
    resource = await _service(request).get_account(actor=actor, account_id=account_id)
    _set_etag(response, resource)
    return resource


@router.patch("/application-accounts/{account_id}", response_model=Account)
async def update_account(
    request: Request,
    response: Response,
    actor: Actor,
    account_id: str,
    body: UpdateAccountRequest,
) -> Account:
    resource = await _service(request).update_account(actor=actor, account_id=account_id, request=body)
    _set_etag(response, resource)
    return resource


@router.put("/application-accounts/{account_id}/credentials", response_model=Account)
async def replace_account_credentials(
    request: Request,
    response: Response,
    actor: Actor,
    account_id: str,
    body: ReplaceAccountCredentialsRequest,
    idempotency_key: IdempotencyKey,
) -> Account:
    resource = await _service(request).replace_credentials(
        actor=actor,
        account_id=account_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    _set_etag(response, resource)
    return resource


@router.post("/application-accounts/{account_id}/{action}", response_model=Account)
async def change_account_lifecycle(
    request: Request,
    response: Response,
    actor: Actor,
    account_id: str,
    action: Literal["enable", "disable"],
    body: AccountCommandRequest,
    idempotency_key: IdempotencyKey,
) -> Account:
    target = AccountStatus.active if action == "enable" else AccountStatus.disabled
    resource = await _service(request).set_status(
        actor=actor,
        account_id=account_id,
        status=target,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
    )
    _set_etag(response, resource)
    return resource


@router.delete("/application-accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    request: Request, actor: Actor, account_id: str, expected_version: Annotated[int, Query(ge=1)]
) -> Response:
    await _service(request).delete_account(actor=actor, account_id=account_id, expected_version=expected_version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/application-accounts/{account_id}/event-connection", response_model=EventConnectionStatus)
async def event_connection(request: Request, actor: Actor, account_id: str) -> EventConnectionStatus:
    return await _service(request).event_connection(actor=actor, account_id=account_id)
