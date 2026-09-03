"""Skill Management public `/api/v1` routes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor, authenticate_request

from .catalog import SkillCatalogService
from .domain import (
    CreateSkillRequest,
    CreateSkillRevisionRequest,
    Skill,
    SkillAgentReferenceCollection,
    SkillCollection,
    SkillPublicationReceipt,
    SkillRevision,
    SkillRevisionCollection,
    SkillUploadReceipt,
    UpdateSkillRequest,
)
from .errors import SkillError
from .package import MAX_ARCHIVE_BYTES
from .publication import SkillPublicationService
from .uploads import SkillUploadService

router = APIRouter(prefix="/api/v1", tags=["skill-management"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=512)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=1, max_length=256)]
_CONTENT_CHUNK_BYTES = 1024 * 1024


def _uploads(request: Request) -> SkillUploadService:
    service: SkillUploadService | None = getattr(request.app.state, "skill_upload_service", None)
    if service is None:
        raise SkillError(
            "skill_management_unavailable",
            "Skill Management is unavailable.",
            status_code=503,
        )
    return service


def _catalog(request: Request) -> SkillCatalogService:
    service: SkillCatalogService | None = getattr(request.app.state, "skill_catalog_service", None)
    if service is None:
        raise SkillError(
            "skill_management_unavailable",
            "Skill Management is unavailable.",
            status_code=503,
        )
    return service


def _publication(request: Request) -> SkillPublicationService:
    service: SkillPublicationService | None = getattr(request.app.state, "skill_publication_service", None)
    if service is None:
        raise SkillError(
            "skill_management_unavailable",
            "Skill Management is unavailable.",
            status_code=503,
        )
    return service


@router.post(
    "/workspaces/{workspace_id}/skill-uploads",
    response_model=SkillUploadReceipt,
    status_code=status.HTTP_201_CREATED,
)
async def stage_skill_upload(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    idempotency_key: IdempotencyKey,
) -> SkillUploadReceipt:
    archive = await _read_zip_body(request)
    result = await _uploads(request).stage(
        actor=actor,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        archive=archive,
    )
    response.status_code = result.status_code
    return result.result


@router.get("/skill-uploads/{upload_id}", response_model=SkillUploadReceipt)
async def get_skill_upload(request: Request, actor: Actor, upload_id: str) -> SkillUploadReceipt:
    return await _uploads(request).get(actor=actor, upload_id=upload_id)


@router.delete("/skill-uploads/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill_upload(request: Request, actor: Actor, upload_id: str) -> Response:
    await _uploads(request).delete(actor=actor, upload_id=upload_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/workspaces/{workspace_id}/skills",
    response_model=SkillPublicationReceipt,
    status_code=status.HTTP_201_CREATED,
)
async def create_skill(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    idempotency_key: IdempotencyKey,
    body: CreateSkillRequest,
) -> SkillPublicationReceipt:
    result = await _publication(request).create(
        actor=actor,
        workspace_id=workspace_id,
        request=body,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["ETag"] = resource_etag(result.result.skill.id, result.result.skill.updated_at)
    return result.result


@router.post(
    "/skills/{skill_id}/revisions",
    response_model=SkillPublicationReceipt,
    status_code=status.HTTP_201_CREATED,
)
async def create_skill_revision(
    request: Request,
    response: Response,
    actor: Actor,
    skill_id: str,
    idempotency_key: IdempotencyKey,
    body: CreateSkillRevisionRequest,
) -> SkillPublicationReceipt:
    result = await _publication(request).publish_revision(
        actor=actor,
        skill_id=skill_id,
        request=body,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    return result.result


@router.get("/workspaces/{workspace_id}/skills", response_model=SkillCollection)
async def list_skills(
    request: Request,
    actor: Actor,
    workspace_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> SkillCollection:
    return await _catalog(request).list(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/skills/{skill_id}", response_model=Skill)
async def get_skill(request: Request, response: Response, actor: Actor, skill_id: str) -> Skill:
    skill = await _catalog(request).get(actor=actor, skill_id=skill_id)
    response.headers["ETag"] = resource_etag(skill.id, skill.updated_at)
    return skill


@router.get("/skills/{skill_id}/revisions", response_model=SkillRevisionCollection)
async def list_skill_revisions(
    request: Request,
    actor: Actor,
    skill_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> SkillRevisionCollection:
    return await _catalog(request).list_revisions(
        actor=actor,
        skill_id=skill_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/skills/{skill_id}/references", response_model=SkillAgentReferenceCollection)
async def list_skill_references(
    request: Request,
    actor: Actor,
    skill_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> SkillAgentReferenceCollection:
    return await _catalog(request).references(
        actor=actor,
        skill_id=skill_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/skill-revisions/{skill_revision_id}", response_model=SkillRevision)
async def get_skill_revision(
    request: Request,
    actor: Actor,
    skill_revision_id: str,
) -> SkillRevision:
    return await _catalog(request).get_revision(actor=actor, revision_id=skill_revision_id)


@router.get("/skill-revisions/{skill_revision_id}/content")
async def get_skill_revision_content(
    request: Request,
    actor: Actor,
    skill_revision_id: str,
) -> Response:
    content, digest = await _catalog(request).content(actor=actor, revision_id=skill_revision_id)
    return StreamingResponse(
        _content_chunks(content),
        media_type="application/zip",
        headers={"ETag": f'W/"sha256:{digest}"'},
    )


@router.patch("/skills/{skill_id}", response_model=Skill)
async def update_skill(
    request: Request,
    response: Response,
    actor: Actor,
    skill_id: str,
    body: UpdateSkillRequest,
    if_match: IfMatch,
) -> Skill:
    skill = await _catalog(request).update(actor=actor, skill_id=skill_id, if_match=if_match, request=body)
    response.headers["ETag"] = resource_etag(skill.id, skill.updated_at)
    return skill


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    request: Request,
    actor: Actor,
    skill_id: str,
    if_match: IfMatch,
) -> Response:
    await _catalog(request).delete(actor=actor, skill_id=skill_id, if_match=if_match)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _read_zip_body(request: Request) -> bytes:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/zip":
        raise SkillError(
            "invalid_request",
            "The request body must be exactly one application/zip package.",
            status_code=400,
        )
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_ARCHIVE_BYTES:
                raise _archive_limit()
        except ValueError as error:
            raise SkillError(
                "invalid_request",
                "Content-Length is invalid.",
                status_code=400,
            ) from error
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_ARCHIVE_BYTES:
            raise _archive_limit()
    return bytes(body)


def _archive_limit() -> SkillError:
    return SkillError(
        "skill_package_limit",
        "The uploaded ZIP exceeds the package size limit.",
        status_code=400,
    )


async def _content_chunks(content: bytes) -> AsyncIterator[bytes]:
    for offset in range(0, len(content), _CONTENT_CHUNK_BYTES):
        yield content[offset : offset + _CONTENT_CHUNK_BYTES]
