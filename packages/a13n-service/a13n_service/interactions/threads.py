"""Thread allocation HTTP boundary."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.environments.websocket.admission import OnlineAdmission
from a13n_service.environments.websocket.coordination import ConnectionCoordination
from a13n_service.http_types import IdempotencyKey
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.iam.http.resource_dependencies import WorkspaceId
from a13n_service.request_runtime import get_process_runtime

from .domain import Thread
from .thread_creation import allocate_thread
from .thread_domain import CreateThreadRequest

router = APIRouter(prefix="/api/v1", tags=["threads"])


@router.post("/workspaces/{workspace}/threads", status_code=201)
async def create_thread(
    request: Request,
    workspace_id: WorkspaceId,
    body: CreateThreadRequest,
    actor: Annotated[AuthenticatedActor, Depends(authenticate_request)],
    idempotency_key: IdempotencyKey,
) -> Thread:
    runtime = get_process_runtime(request)
    if runtime is None or runtime.control is None:
        raise ApplicationError(
            "control_unavailable", "Thread control is unavailable", category=ErrorCategory.unavailable
        )
    return await allocate_thread(
        runtime.shared.storage.sessions,
        actor=actor,
        workspace_id=workspace_id,
        body=body,
        idempotency_key=idempotency_key,
        admission=OnlineAdmission(
            runtime.shared.storage.sessions,
            ConnectionCoordination(runtime.shared.storage.redis),
            devices=runtime.control.environments.devices,
        ),
    )
