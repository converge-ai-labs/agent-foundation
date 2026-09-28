"""Bounded, rate-limited multipart staging."""

from typing import Annotated

from fastapi import APIRouter, File, Request

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import IdempotencyKey
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.resources.uploads import service
from a13n_service.resources.uploads.schemas import Upload, UploadCreate
from a13n_service.tenancy.requests import Actor, WorkspaceId, limit_uploads

router = APIRouter(prefix="/api/v1/uploads", tags=["assets"])


@router.post("", response_model=Upload)
async def create_upload(
    request: Request,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
    body: Annotated[UploadCreate, File()],
    idempotency_key: IdempotencyKey,
) -> Upload:
    """Repeating a request with the same `Idempotency-Key` and bytes returns the same upload."""
    await limit_uploads(request, actor.id)
    limits = runtime.settings.objects
    bound = min(limits.upload_bytes, limits.max_bytes)
    file = body.file
    content = await file.read(bound + 1)
    if len(content) > bound:
        raise ServiceError("payload_too_large", "Upload exceeds its byte limit", {"limit": bound})
    return await service.stage(
        runtime.storage,
        runtime.objects,
        actor,
        workspace_id,
        request_key=idempotency_key,
        filename=file.filename or "",
        content_type=file.content_type or "",
        content=content,
    )
