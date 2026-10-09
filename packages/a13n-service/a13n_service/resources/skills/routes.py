"""Skill heads, revisions and package content."""

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from a13n_service.infra.http import IfMatch, PageLimit, download_headers, tagged
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.resources.revisions import Search
from a13n_service.resources.runtime import Runtime
from a13n_service.resources.skills import content, service
from a13n_service.resources.skills.github import GitHub
from a13n_service.resources.skills.schemas import (
    GitHubSource,
    Skill,
    SkillCreate,
    SkillManifest,
    SkillPage,
    SkillRevision,
    SkillRevisionCreate,
    SkillRevisionPage,
    SkillUpdate,
    SkillValidate,
    SourceKind,
    UploadSource,
)
from a13n_service.tenancy.requests import Actor, WorkspaceId, limit_uploads

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


async def _github(request: Request, actor: Actor, runtime: Runtime, source: UploadSource | GitHubSource) -> GitHub:
    """The importer; reading from GitHub counts against the caller's upload budget, as the stored archive does."""
    if isinstance(source, GitHubSource):
        await limit_uploads(request, actor.id)
    settings = runtime.settings
    return GitHub(
        policy=runtime.endpoint_policy,
        timeout=settings.control.import_timeout,
        max_bytes=settings.objects.max_bytes,
    )


@router.post("", response_model=Skill, status_code=201, openapi_extra={"x-a13n-mcp": True})
async def create_skill(
    request: Request,
    response: Response,
    workspace_id: WorkspaceId,
    body: SkillCreate,
    actor: Actor,
    runtime: CurrentRuntime,
) -> Skill:
    github = await _github(request, actor, runtime, body.source)
    result = await service.create_skill(runtime.storage, runtime.objects, github, actor, workspace_id, body)
    return tagged(response, result)


@router.post("/validate", response_model=SkillManifest, openapi_extra={"x-a13n-mcp": True})
async def validate_package(
    request: Request, workspace_id: WorkspaceId, body: SkillValidate, actor: Actor, runtime: CurrentRuntime
) -> SkillManifest:
    """The manifest the package would give a new skill or revision, checked as creation checks it; nothing is
    stored."""
    github = await _github(request, actor, runtime, body.source)
    return await service.validate_package(runtime.storage, runtime.objects, github, actor, workspace_id, body.source)


@router.get("", response_model=SkillPage, openapi_extra={"x-a13n-mcp": True})
async def list_skills(
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
    label: Annotated[list[str] | None, Query()] = None,
    q: Annotated[Search | None, Query()] = None,
    source: SourceKind | None = None,
    archived: bool | None = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> SkillPage:
    """Skills of the workspace. `q` matches the name or description, ignoring case; `source` the kind of
    source the default revision was read from; `archived` keeps only archived skills, or only open ones."""
    return await service.list_skills(
        runtime.storage,
        actor,
        workspace_id,
        labels=label or [],
        q=q,
        source=source,
        archived=archived,
        limit=limit,
        cursor=cursor,
    )


@router.get("/{skill_id}", response_model=Skill, openapi_extra={"x-a13n-mcp": True})
async def get_skill(
    response: Response, workspace_id: WorkspaceId, skill_id: str, actor: Actor, runtime: CurrentRuntime
) -> Skill:
    return tagged(response, await service.get_skill(runtime.storage, actor, workspace_id, skill_id))


@router.patch("/{skill_id}", response_model=Skill, openapi_extra={"x-a13n-mcp": True})
async def update_skill(
    response: Response,
    workspace_id: WorkspaceId,
    skill_id: str,
    body: SkillUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Skill:
    """Name, description and labels; an archived skill changes only by unarchiving."""
    result = await service.update_skill(runtime.storage, actor, workspace_id, skill_id, body, if_match=if_match)
    return tagged(response, result)


@router.post("/{skill_id}/archive", response_model=Skill, openapi_extra={"x-a13n-mcp": True})
async def archive_skill(
    response: Response,
    workspace_id: WorkspaceId,
    skill_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Skill:
    """Archived skills keep their revisions readable and pinned; they refuse new revisions and new pins."""
    result = await service.set_archived(
        runtime.storage, actor, workspace_id, skill_id, archived=True, if_match=if_match
    )
    return tagged(response, result)


@router.post("/{skill_id}/unarchive", response_model=Skill, openapi_extra={"x-a13n-mcp": True})
async def unarchive_skill(
    response: Response,
    workspace_id: WorkspaceId,
    skill_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Skill:
    result = await service.set_archived(
        runtime.storage, actor, workspace_id, skill_id, archived=False, if_match=if_match
    )
    return tagged(response, result)


@router.post("/{skill_id}/revisions", response_model=SkillRevision, status_code=201, openapi_extra={"x-a13n-mcp": True})
async def create_revision(
    request: Request,
    workspace_id: WorkspaceId,
    skill_id: str,
    body: SkillRevisionCreate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> SkillRevision:
    """A package whose manifest equals the default revision's creates nothing and returns that revision."""
    github = await _github(request, actor, runtime, body.source)
    return await service.create_revision(
        runtime.storage, runtime.objects, github, actor, workspace_id, skill_id, body, if_match=if_match
    )


@router.get("/{skill_id}/revisions", response_model=SkillRevisionPage, openapi_extra={"x-a13n-mcp": True})
async def list_revisions(
    workspace_id: WorkspaceId,
    skill_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> SkillRevisionPage:
    return await service.list_revisions(runtime.storage, actor, workspace_id, skill_id, limit=limit, cursor=cursor)


@router.get("/{skill_id}/revisions/{revision_id}", response_model=SkillRevision, openapi_extra={"x-a13n-mcp": True})
async def get_revision(
    workspace_id: WorkspaceId, skill_id: str, revision_id: str, actor: Actor, runtime: CurrentRuntime
) -> SkillRevision:
    return await service.get_revision(runtime.storage, actor, workspace_id, skill_id, revision_id)


@router.post(
    "/{skill_id}/revisions/{revision_id}/set-default", response_model=Skill, openapi_extra={"x-a13n-mcp": True}
)
async def set_default_revision(
    response: Response,
    workspace_id: WorkspaceId,
    skill_id: str,
    revision_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Skill:
    result = await service.set_default_revision(
        runtime.storage, actor, workspace_id, skill_id, revision_id, if_match=if_match
    )
    return tagged(response, result)


@router.get(
    "/{skill_id}/revisions/{revision_id}/content",
    response_class=Response,
    responses={
        200: {
            "description": "The revision's package as a zip archive",
            "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def read_archive(
    workspace_id: WorkspaceId, skill_id: str, revision_id: str, actor: Actor, runtime: CurrentRuntime
) -> Response:
    """The revision's package as a zip archive."""
    filename, data = await content.read_archive(
        runtime.storage, runtime.objects, actor, workspace_id, skill_id, revision_id
    )
    return Response(
        data,
        media_type="application/zip",
        headers=download_headers(filename),
    )


@router.get(
    "/{skill_id}/revisions/{revision_id}/files/{path:path}",
    response_class=Response,
    responses={
        200: {
            "description": "One file of the revision's package",
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def read_file(
    workspace_id: WorkspaceId, skill_id: str, revision_id: str, path: str, actor: Actor, runtime: CurrentRuntime
) -> Response:
    """One package file, by the path the revision's manifest lists."""
    data = await content.read_file(runtime.storage, runtime.objects, actor, workspace_id, skill_id, revision_id, path)
    return Response(data, media_type="application/octet-stream", headers=download_headers(None))
