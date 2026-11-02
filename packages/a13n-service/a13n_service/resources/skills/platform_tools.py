"""Explicit internal business operations; HTTP admission does not expand this surface."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic.experimental.missing_sentinel import MISSING

from a13n_service.infra.http import PageLimit
from a13n_service.infra.labels import Labels
from a13n_service.platform_tools import ToolContext as PlatformContext
from a13n_service.platform_tools import definition
from a13n_service.resources.revisions import Search
from a13n_service.resources.runtime import Runtime
from a13n_service.resources.skills import service
from a13n_service.resources.skills.requests import package_importer
from a13n_service.resources.skills.schemas import (
    Description,
    Skill,
    SkillCreate,
    SkillManifest,
    SkillName,
    SkillPage,
    SkillRevision,
    SkillRevisionCreate,
    SkillRevisionPage,
    SkillUpdate,
    SkillValidate,
    SourceKind,
)

type ToolContext = PlatformContext[Runtime]


class SkillMetadata(BaseModel):
    """Omitted metadata is retained; empty description/labels explicitly clear it."""

    model_config = ConfigDict(extra="forbid")
    name: SkillName | MISSING = MISSING
    description: Description | MISSING = MISSING
    labels: Labels | MISSING = MISSING


async def create_skill(ctx: ToolContext, *, body: SkillCreate) -> Skill:
    """Import a validated GitHub or uploaded Skill package and publish its initial immutable revision atomically."""
    request = ctx.request
    github = await package_importer(request, ctx.actor, ctx.runtime, body.source)
    return await service.create_skill(
        ctx.runtime.storage, ctx.runtime.objects, github, ctx.actor, ctx.workspace_id, body
    )


async def validate_skill_source(ctx: ToolContext, *, body: SkillValidate) -> SkillManifest:
    """The manifest the package would give a new skill or revision, checked as creation checks it; nothing is
    stored."""
    request = ctx.request
    github = await package_importer(request, ctx.actor, ctx.runtime, body.source)
    return await service.validate_package(
        ctx.runtime.storage, ctx.runtime.objects, github, ctx.actor, ctx.workspace_id, body.source
    )


async def list_skills(
    ctx: ToolContext,
    *,
    label: Annotated[list[str] | None, Field()] = None,
    q: Annotated[Search | None, Field()] = None,
    source: SourceKind | None = None,
    archived: bool | None = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> SkillPage:
    """Skills of the workspace. `q` matches the name or description, ignoring case; `source` the kind of
    source the default revision was read from; `archived` keeps only archived skills, or only open ones."""
    return await service.list_skills(
        ctx.runtime.storage,
        ctx.actor,
        ctx.workspace_id,
        labels=label or [],
        q=q,
        source=source,
        archived=archived,
        limit=limit,
        cursor=cursor,
    )


async def get_skill(ctx: ToolContext, *, skill_id: str) -> Skill:
    """Read Skill metadata, its default revision and resource version before changing metadata or source."""
    return await service.get_skill(ctx.runtime.storage, ctx.actor, ctx.workspace_id, skill_id)


async def update_skill_metadata(
    ctx: ToolContext, *, skill_id: str, body: SkillMetadata, expected_version: str
) -> Skill:
    """Name, description and labels; an archived skill changes only by unarchiving."""
    return await service.update_skill(
        ctx.runtime.storage,
        ctx.actor,
        ctx.workspace_id,
        skill_id,
        SkillUpdate.model_validate(body.model_dump(exclude_unset=True)),
        if_match=expected_version,
    )


async def archive_skill(ctx: ToolContext, *, skill_id: str, expected_version: str) -> Skill:
    """Archived skills keep their revisions readable and pinned; they refuse new revisions and new pins."""
    return await service.set_archived(
        ctx.runtime.storage, ctx.actor, ctx.workspace_id, skill_id, archived=True, if_match=expected_version
    )


async def unarchive_skill(ctx: ToolContext, *, skill_id: str, expected_version: str) -> Skill:
    """Restore a Skill at the observed version so it can accept new revisions and new Agent pins."""
    return await service.set_archived(
        ctx.runtime.storage, ctx.actor, ctx.workspace_id, skill_id, archived=False, if_match=expected_version
    )


async def update_skill_source(
    ctx: ToolContext, *, skill_id: str, body: SkillRevisionCreate, expected_version: str
) -> service.SkillSourceChange:
    """A package whose manifest equals the default revision's creates nothing and returns that revision."""
    request = ctx.request
    github = await package_importer(request, ctx.actor, ctx.runtime, body.source)
    return await service.change_source(
        ctx.runtime.storage,
        ctx.runtime.objects,
        github,
        ctx.actor,
        ctx.workspace_id,
        skill_id,
        body,
        if_match=expected_version,
    )


async def list_skill_revisions(
    ctx: ToolContext, *, skill_id: str, limit: PageLimit = 50, cursor: str | None = None
) -> SkillRevisionPage:
    """List immutable Skill revisions with pagination; use revision IDs to preserve exact Agent pins."""
    return await service.list_revisions(
        ctx.runtime.storage, ctx.actor, ctx.workspace_id, skill_id, limit=limit, cursor=cursor
    )


async def get_skill_revision(ctx: ToolContext, *, skill_id: str, revision_id: str) -> SkillRevision:
    """Read the source, manifest and digest of one immutable Skill revision without changing its default selection."""
    return await service.get_revision(ctx.runtime.storage, ctx.actor, ctx.workspace_id, skill_id, revision_id)


async def set_default_skill_revision(
    ctx: ToolContext, *, skill_id: str, revision_id: str, expected_version: str
) -> Skill:
    """Select an existing Skill revision as default at the observed Skill version; existing Agent revision pins remain unchanged."""
    return await service.set_default_revision(
        ctx.runtime.storage, ctx.actor, ctx.workspace_id, skill_id, revision_id, if_match=expected_version
    )


TOOLS = (
    definition(create_skill, group="skills", effect="write"),
    definition(validate_skill_source, group="skills", effect="read"),
    definition(list_skills, group="skills", effect="read"),
    definition(get_skill, group="skills", effect="read"),
    definition(update_skill_metadata, group="skills", effect="write"),
    definition(archive_skill, group="skills", effect="write"),
    definition(unarchive_skill, group="skills", effect="write"),
    definition(update_skill_source, group="skills", effect="write"),
    definition(list_skill_revisions, group="skills", effect="read"),
    definition(get_skill_revision, group="skills", effect="read"),
    definition(set_default_skill_revision, group="skills", effect="write"),
)
