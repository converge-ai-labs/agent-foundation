"""Run-scoped acceptance and bounded reads for additional Environment mounts."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from a13n_service.application_errors import ErrorCategory
from a13n_service.http_types import IdempotencyKey
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.request_runtime import get_control_runtime, get_process_runtime

from .domain import Collection
from .errors import EnvironmentManagementError
from .mount_domain import AddEnvironmentMountRequest, RunEnvironmentMount
from .mounts import RunEnvironmentMountService

router = APIRouter()
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]


def _service(request: Request) -> RunEnvironmentMountService:
    control = get_control_runtime(request)
    if control is None:
        raise EnvironmentManagementError(
            "environment_unavailable", "Environment control is unavailable", category=ErrorCategory.unavailable
        )
    return control.environment_mounts


@router.post("/runs/{run_id}/environment-mounts", status_code=201)
async def add_mount(
    request: Request, actor: Actor, run_id: str, body: AddEnvironmentMountRequest, idempotency_key: IdempotencyKey
) -> RunEnvironmentMount:
    runtime = get_process_runtime(request)
    if runtime is None or runtime.status.draining:
        raise EnvironmentManagementError(
            "environment_unavailable", "Environment control is unavailable", category=ErrorCategory.unavailable
        )
    return await _service(request).add(actor=actor, run_id=run_id, idempotency_key=idempotency_key, request=body)


@router.get("/runs/{run_id}/environment-mounts")
async def list_mounts(
    request: Request,
    actor: Actor,
    run_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> Collection[RunEnvironmentMount]:
    return await _service(request).list(actor=actor, run_id=run_id, limit=limit, cursor=cursor)
